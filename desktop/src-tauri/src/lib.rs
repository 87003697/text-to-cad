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

use std::sync::Mutex;

use tauri::{Manager, RunEvent};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

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

/// Spawn the Python backend sidecar and navigate the main window to the loopback
/// URL it announces (`CAD_VIEWER_URL=http://127.0.0.1:<port>/`).
fn start_backend(app: &tauri::AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    let resource_dir = app.path().resource_dir()?;
    let runtime_dir = resource_dir.join("runtime");
    let dist_root = runtime_dir.join("dist");

    // The sidecar binary is the relocated venv interpreter; point it at the bundled
    // server_py + site-packages. Exact env is validated at the Phase-0 packaging gate.
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
        .env("PYTHONPATH", runtime_dir.to_string_lossy().to_string())
        .args(args);

    let (mut rx, child) = command.spawn()?;
    app.state::<BackendProcess>().0.lock().unwrap().replace(child);

    let handle = app.clone();
    tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
            if let CommandEvent::Stdout(bytes) = event {
                let line = String::from_utf8_lossy(&bytes);
                if let Some(url) = line.trim().strip_prefix("CAD_VIEWER_URL=") {
                    if let (Ok(parsed), Some(window)) =
                        (url.parse::<tauri::Url>(), handle.get_webview_window("main"))
                    {
                        let _ = window.navigate(parsed);
                    }
                    break; // backend is up; further stdout is just logging
                }
            }
        }
    });
    Ok(())
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
            if !cfg!(debug_assertions) {
                if let Err(err) = start_backend(app.handle()) {
                    eprintln!("[cad-viewer-desktop] failed to start backend sidecar: {err}");
                }
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building the CAD Viewer desktop shell")
        .run(|app, event| {
            // Kill the backend sidecar when the app exits so no orphan Python/OCP
            // process is left behind.
            if let RunEvent::Exit = event {
                if let Some(child) = app.state::<BackendProcess>().0.lock().unwrap().take() {
                    let _ = child.kill();
                }
            }
        });
}
