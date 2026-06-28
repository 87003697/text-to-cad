# CAD Viewer desktop shell (Tauri)

A native desktop wrapper around the CAD Viewer that runs the **same Python CAD
engine the agent uses** — full OCP/cadpy STEP build & export — with no process
interdependency between the agent's STEP CLI and the viewer.

> **Status: scaffold.** The Python pieces here are implemented and verified (see
> "What is verified"). The Rust shell and the per-platform packaging are written
> against the Tauri 2 APIs but require `cargo` / `tauri` / `uv`, which were not
> available where this was authored. Build and verify them at the gate below.

## Architecture

```
┌───────────────────────────── Tauri (Rust) ─────────────────────────────┐
│  thin shell: window + lifecycle + native OS dialogs/openers             │
│                                                                         │
│  prod: spawn ──► server_py.server  (sidecar, 127.0.0.1:<ephemeral>)     │
│        read  ──► "CAD_VIEWER_URL=…" on stdout                           │
│        navigate the window to that loopback URL                         │
└─────────────────────────────────┬───────────────────────────────────────┘
                                  │ manages (in-process)
                     ┌────────────▼─────────────┐
                     │  warm-OCCT worker         │  server_py/worker.py
                     │  (persistent, stdio RPC)  │  + worker_client.py
                     └────────────┬─────────────┘
                                  │ calls (in-process)
                     ┌────────────▼─────────────┐
                     │  cadpy cores (GENERAL)    │  build_step_artifact /
                     │  build_step_artifact,     │  export_model_to_path —
                     │  export_model_to_path     │  the SAME callables the
                     └───────────────────────────┘  agent CLI uses
```

The agent's STEP CLI (`python -m cadpy.step_artifact …`) and the viewer's worker
are **peer entrypoints onto the shared `cadpy` callables**. They never talk to
each other as processes — the only thing they share is the passive filesystem
(`__cadcache__`). This satisfies the hard requirement: same engine, no active
coupling, fully runnable by agents from skills/tools.

### Why "spawn the Python backend over loopback" (primary path)

The `server_py` HTTP backend is already byte-fidelity-verified (catalog, asset
serving, artifact status, save dialog) and already manages the warm worker. The
desktop shell reuses it wholesale: ~one screen of Rust to spawn it on
`127.0.0.1:0`, read the announced URL, and load it. Maximum reuse, minimum
new surface.

Security posture: the port binds to loopback only and is ephemeral
(`--port 0`). Hardening (a per-launch token, or eliminating the port entirely)
is the `cad://` path below.

### Alternative: the `cad://` custom protocol (no network port)

A hardened variant serves the SPA and `/__cad/*` through a Tauri custom URI
scheme handler (`register_asynchronous_uri_scheme_protocol`) instead of a
loopback HTTP server — the webview makes no network connection at all. Rust
reads files from disk (with `Range`/`206` for large GLBs) and forwards control
calls (catalog, build, export) to the worker over stdio. This requires
re-expressing the `server_py` HTTP surface as worker RPC methods + a Rust
protocol handler, so it is deferred until the loopback path is proven. The
worker, the packaging, and the OS plugins below are identical for both paths.

## Layout

| path | role |
|------|------|
| `src-tauri/tauri.conf.json` | window, `externalBin` sidecar, `resources`, file associations, capabilities |
| `src-tauri/src/lib.rs` | spawn sidecar → read `CAD_VIEWER_URL=` → navigate window; kill on exit |
| `src-tauri/capabilities/default.json` | ACL: `shell:allow-spawn` the sidecar, dialog/opener |
| `src-tauri/python/` | **generated** (gitignored): the relocatable runtime + sidecar binary |
| `splash/` | loading page shown while OCP warms up |
| `../scripts/desktop/build-python-runtime.sh` | builds `src-tauri/python/` (Phase 0) |

## Build & verify (the gate)

Prereqs: Rust + `cargo`, the Tauri CLI (`npm i` in this dir), and `uv`.

One-time setup: generate the app icons referenced by `tauri.conf.json`
(`npm --prefix . run tauri icon path/to/logo.png` → `src-tauri/icons/`). The
loopback primary path needs **no** frontend changes — the SPA talks to the
backend over normal `fetch` exactly as in the browser; `@tauri-apps/api`
(`invoke`, `convertFileSrc`) is only needed for the `cad://` hardening path.

```bash
# 1. Build the SPA the backend serves
npm --prefix ../viewer run build

# 2. Phase 0 — build the relocatable Python runtime + sidecar for this platform
npm --prefix . run build:runtime           # -> src-tauri/python/{binaries,runtime}
src-tauri/python/binaries/cad-viewer-backend-* \
  -c 'import cadpy, OCP, build123d; print("engine OK")'   # smoke test the engine

# 3. Dev (hot-reload UI against the Python backend via vite's proxy)
npm --prefix . run dev

# 4. Production bundle
npm --prefix . run build                    # -> .app / .dmg / .msi / .deb / .AppImage
```

### Phase 0 risk gate (do this first)

OCP packaging is the highest-risk piece; prove it before investing in UI polish:

1. `cadquery-ocp-novtk` installs (we drop VTK because all rendering is in the JS
   frontend) and imports from a **relocated** bundle. `build-python-runtime.sh`
   runs this automatically: after building it copies `binaries/` + `runtime/` to a
   fresh temp path and imports `cadpy, OCP, build123d` there THROUGH THE SHIM,
   failing the build if a moved bundle can't import. The bundle is self-contained —
   the full python-build-standalone install (`runtime/cpython/`) + site-packages —
   and `externalBin` is a launcher shim that execs the bundled interpreter, which
   self-resolves its lib/stdlib (a bare venv binary cannot — see the verified
   section). **Validated on macOS arm64.**
2. A real STEP build runs from the bundled interpreter
   (`python -m cadpy.step_artifact …` against a fixture) and matches the agent
   CLI byte-for-byte.
3. **macOS**: every OCP `.dylib` and the embedded interpreter must be
   codesigned (`--deep` is insufficient — sign nested dylibs individually) and
   the app notarized. This is the known-hard step; budget for it.
4. App size is hundreds of MB (OCP is large). Acceptable; note it.
5. `cadquery-ocp` supports CPython **3.9–3.12** only — pin 3.12, not 3.13.

### Per-platform CI

`.github/workflows/desktop-build.yml` (manual `workflow_dispatch`) builds every
leg on its native-arch runner — macOS arm64 (`macos-14`) + x86_64 (`macos-13`),
Linux x86_64 (`ubuntu-22.04`), Windows x86_64 (`windows-latest`) — so each installs
its own architecture's OCP wheels (no cross-arch wheel problems). Each leg builds
the SPA, runs `build-python-runtime.sh --target <triple>` (incl. the relocated
smoke test), bundles via `tauri-apps/tauri-action@v0`, and uploads the installers.
macOS signing/notarization runs only when the `APPLE_*` secrets are set; otherwise
the build is unsigned. It is standalone and does NOT publish a GitHub Release
(releases go through the `Release` workflow per AGENTS.md) — fold it in there when
the desktop app graduates from scaffold, and never bump `plugins/cad/VERSION` by
hand (the release pipeline stamps versions).

Status caveat: the workflow + cross-platform packaging are written and statically
checked but not run on real runners here. Known watch-items at first run: GitHub
may have retired the `macos-13` (Intel) runner; and the Windows leg's venv
relocation (bundling `python.exe` + its DLLs) is the least-validated path.

## What is verified (here) vs gated (needs toolchain)

**Verified** (Python, tested in this repo):
- `cadpy` shared cores callable in-process, content-identical to the cold CLI.
- The warm worker (`server_py/worker.py` + `worker_client.py`): warm builds match
  cold on the recorded closure; respawn / recycle / cold-fallback; 12 tests.
- `server_py.server --port 0 --announce-url`: binds an ephemeral loopback port
  and prints `CAD_VIEWER_URL=…`; catalog serves over it. This is the exact
  handshake `lib.rs` performs.
- The packaging script's structure, arg handling, and toolchain checks.

**Built + run for real on macOS arm64** (rustup 1.96 + uv 0.11.25, this machine):
- `lib.rs` compiles against the Tauri 2 crates; `cargo build` + `tauri build --debug`
  produce `CAD Viewer.app` (+ `.dmg`).
- **The interpreter-relocation problem is SOLVED.** A uv venv is not self-contained
  (its `python3` finds `libpython`/stdlib relative to the pbs base — copying the bare
  binary dies with `dyld: libpython3.12.dylib not loaded`). The fix: bundle the FULL
  self-contained python-build-standalone install (`runtime/cpython/`) + the
  site-packages, and make `externalBin` a **launcher shim** (a `/bin/sh` script) that
  finds the runtime next to itself (`../runtime` in the smoke test, `../Resources/
  runtime` in the `.app`), sets `PYTHONPATH`, and execs the bundled interpreter — which
  self-resolves its lib/stdlib. The relocated smoke test passes; inside the built
  `.app` the shim imports `cadpy, OCP, build123d` and runs the backend.
- End-to-end packaged launch works: `open CAD Viewer.app` → spawns the bundled
  sidecar → backend on an ephemeral loopback port → window navigates to it with
  `?dir=<models>` → the 265-model workspace renders, served by the app's own warm
  worker. `tauri::is_dev()` (not `debug_assertions`) gates dev vs bundled so a
  `--debug` bundle still takes the sidecar path.
- Failure hardening (from the scaffold review): spawn error / 30s startup timeout /
  exit-before-announce / post-nav crash each raise a native error dialog instead of a
  silent spinner; the splash explains a standalone-open / slow start. `server_py`
  documents its loopback, no-auth trust model.

**Still gated** (needs a real signing identity / other runners):
- macOS **codesigning + notarization** of the bundled OCP/cpython dylibs (the local
  `.app` is unsigned/ad-hoc — fine to run locally, not for distribution).
- The **Windows + Linux** legs of `build-python-runtime.sh` — the self-contained-runtime
  + shim redesign is implemented for posix/macOS; Windows still errors out (it needs the
  Windows cpython layout + a `.bat`/`.exe` shim), and Linux is built-but-unrun-here.
- App icons: generated from a logo via `npm run tauri icon <logo>` (one-time, see
  above); only a placeholder was used here, so `src-tauri/icons/` is gitignored and CI
  needs real icons (or an icon step).
- Remaining minor polish: a React error boundary in the SPA for catastrophic render
  failures (backend-connectivity errors are already handled in-app).
