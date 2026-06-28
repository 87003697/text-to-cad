//! CAD Viewer desktop shell (Tauri 2).
//!
//! Architecture (see desktop/README.md): the desktop app runs the SAME Python CAD
//! engine the agent uses. In production it spawns the bundled `server_py` backend
//! as a sidecar bound to an ephemeral loopback port; that backend serves the SPA
//! and `/__cad/*`, and internally manages the persistent warm-OCCT worker for STEP
//! build/export. The Rust shell stays thin: spawn the sidecar, read the announced
//! URL from its stdout, point the window at it, and tear it down on exit. There is
//! NO process channel between this shell and the agent's STEP CLI — both reach the
//! shared cadpy callables independently.
//!
//! NOTE: this is the Phase-2 scaffold. It is structured against the Tauri 2 APIs
//! but has not been `cargo build`-verified in this environment (no Rust toolchain).
//! Build/verify at the Phase-0 gate: `npm --prefix desktop run tauri build`.

use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::Duration;

use tauri::{Manager, RunEvent};
use tauri_plugin_dialog::{DialogExt, MessageDialogKind};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

/// How long to wait for the backend to announce its URL before declaring failure.
const BACKEND_STARTUP_TIMEOUT: Duration = Duration::from_secs(30);

/// Holds the backend sidecar child so it can be killed on app exit.
#[derive(Default)]
struct BackendProcess(Mutex<Option<CommandChild>>);

/// The served model root. Scaffold default: `$CAD_VIEWER_DIR` or the user's home
/// directory; a real build wires this to a directory picker + recent-files.
fn models_dir() -> String {
    std::env::var("CAD_VIEWER_DIR")
        .ok()
        .or_else(|| std::env::var("HOME").ok())
        .unwrap_or_else(|| ".".to_string())
}

/// Locate the bundled venv's site-packages (where cadpy / OCP / build123d install).
/// The sidecar interpreter is relocated out of the venv, so it cannot find these on
/// its own — we add them to PYTHONPATH explicitly. Handles the posix
/// (`lib/python3.X/site-packages`) and Windows (`Lib/site-packages`) layouts.
fn venv_site_packages(venv: &Path) -> Option<PathBuf> {
    let windows = venv.join("Lib").join("site-packages");
    if windows.is_dir() {
        return Some(windows);
    }
    for entry in std::fs::read_dir(venv.join("lib")).ok()?.flatten() {
        if entry.file_name().to_string_lossy().starts_with("python3") {
            let sp = entry.path().join("site-packages");
            if sp.is_dir() {
                return Some(sp);
            }
        }
    }
    None
}

fn show_error(app: &tauri::AppHandle, message: impl Into<String>) {
    app.dialog()
        .message(message.into())
        .title("CAD Viewer")
        .kind(MessageDialogKind::Error)
        .show(|_| {});
}

/// Spawn the Python backend sidecar and navigate the main window to the loopback
/// URL it announces (`CAD_VIEWER_URL=http://127.0.0.1:<port>/`). Surfaces every
/// failure mode to the user (spawn error, startup timeout, premature exit, and a
/// post-navigation crash) instead of leaving the window stuck on the splash.
fn start_backend(app: &tauri::AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    let resource_dir = app.path().resource_dir()?;
    let runtime_dir = resource_dir.join("runtime");
    let dist_root = runtime_dir.join("dist");
    let venv = runtime_dir.join(".venv");

    // The sidecar binary is the relocated venv interpreter; it resolves server_py via
    // the runtime root and the third-party packages (cadpy/OCP/build123d) via the
    // venv site-packages. PYTHONHOME points it at the venv as a fallback. The EXACT
    // relocation env is the Phase-0 packaging gate (desktop/README.md).
    let mut pythonpath = runtime_dir.to_string_lossy().into_owned();
    if let Some(site) = venv_site_packages(&venv) {
        pythonpath = format!("{}{}{}", pythonpath, sep(), site.to_string_lossy());
    }

    let args: Vec<String> = vec![
        "-m".into(),
        "server_py.server".into(),
        "--dir".into(),
        models_dir(),
        "--host".into(),
        "127.0.0.1".into(),
        "--port".into(),
        "0".into(),
        "--announce-url".into(),
        "--dist-root".into(),
        dist_root.to_string_lossy().into_owned(),
    ];
    let command = app
        .shell()
        .sidecar("cad-viewer-backend")?
        .current_dir(runtime_dir.clone())
        .env("PYTHONPATH", pythonpath)
        .env("PYTHONHOME", venv.to_string_lossy().into_owned())
        .args(args);

    let (mut rx, child) = command.spawn()?;
    app.state::<BackendProcess>().0.lock().unwrap().replace(child);

    let handle = app.clone();
    tauri::async_runtime::spawn(async move {
        // Wait (bounded) for the announce line. Returns Some(url), or None if the
        // backend exited / closed its pipe before announcing.
        let read_url = async {
            while let Some(event) = rx.recv().await {
                match event {
                    CommandEvent::Stdout(bytes) => {
                        let line = String::from_utf8_lossy(&bytes);
                        if let Some(url) = line.trim().strip_prefix("CAD_VIEWER_URL=") {
                            return Some(url.to_string());
                        }
                    }
                    CommandEvent::Terminated(_) => return None,
                    _ => {}
                }
            }
            None
        };

        match tokio::time::timeout(BACKEND_STARTUP_TIMEOUT, read_url).await {
            Ok(Some(url)) => {
                match (url.parse::<tauri::Url>(), handle.get_webview_window("main")) {
                    (Ok(parsed), Some(window)) => {
                        let _ = window.navigate(parsed);
                    }
                    _ => show_error(&handle, "The CAD backend announced an unusable URL."),
                }
                // Keep draining stdout (so the pipe never blocks the backend) and
                // surface a crash AFTER navigation instead of a silently dead page.
                while let Some(event) = rx.recv().await {
                    if let CommandEvent::Terminated(_) = event {
                        show_error(
                            &handle,
                            "The CAD engine stopped unexpectedly. Please restart the app.",
                        );
                        break;
                    }
                }
            }
            Ok(None) => show_error(
                &handle,
                "The CAD engine exited before it finished starting. Check the application logs.",
            ),
            Err(_) => show_error(
                &handle,
                "The CAD engine did not start within 30 seconds. It may be missing or failed to load OpenCASCADE.",
            ),
        }
    });
    Ok(())
}

#[cfg(windows)]
fn sep() -> &'static str {
    ";"
}
#[cfg(not(windows))]
fn sep() -> &'static str {
    ":"
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .manage(BackendProcess::default())
        .setup(|app| {
            // The "main" window is declared in tauri.conf.json. In dev it loads
            // devUrl (vite, which proxies /__cad to a Python backend) — no sidecar.
            // In production it starts on the bundled splash (frontendDist); we spawn
            // the sidecar and navigate the window to the loopback URL it announces.
            //
            // NOTE: capabilities/default.json's shell:allow-spawn allowlist gates the
            // JS Command API, not this trusted Rust ShellExt call (which is unrestricted
            // by design); the allowlist stands as deployment/audit policy.
            if !cfg!(debug_assertions) {
                if let Err(err) = start_backend(app.handle()) {
                    show_error(
                        app.handle(),
                        format!("Could not start the CAD backend: {err}"),
                    );
                }
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building the CAD Viewer desktop shell")
        .run(|app, event| {
            // Kill the backend sidecar when the app exits so no orphan Python/OCP
            // process is left behind. CommandChild::kill() is synchronous in
            // tauri-plugin-shell 2.x (returns Result), so no await is needed.
            if let RunEvent::Exit = event {
                if let Some(child) = app.state::<BackendProcess>().0.lock().unwrap().take() {
                    let _ = child.kill();
                }
            }
        });
}
