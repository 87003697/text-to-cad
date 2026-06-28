#!/usr/bin/env bash
set -euo pipefail

# Build the relocatable Python runtime that the desktop (Tauri) shell bundles and
# runs as its CAD backend sidecar. This is the SAME engine the agent uses: it
# installs the repo's cadpy (STEP build/export core) plus the viewer's server_py
# (which manages the warm-OCCT worker), so the desktop app and the agent CLI share
# one code path with no process interdependency between them.
#
# Toolchain: uv + python-build-standalone (relocatable interpreter; NOT a
# PyInstaller onefile). OCP ships via cadquery-ocp-novtk — VTK is dropped because
# all rendering happens in the JS frontend.
#
# Output layout (consumed by desktop/src-tauri/tauri.conf.json):
#   <out>/binaries/cad-viewer-backend-<target-triple>   # externalBin (the python)
#   <out>/runtime/                                       # bundle.resources
#     ├── .venv/                 site-packages: cadpy, cadpy_metadata, ocp, build123d
#     ├── server_py/             the HTTP backend + warm worker (from viewer/)
#     └── dist/                  the built SPA (from viewer/dist)
#
# This script is the Phase 0 risk gate: run it per platform in CI. Relocating a
# venv + notarizing the OCP dylibs on macOS is the known-hard part — see
# desktop/README.md.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_VERSION="${CAD_DESKTOP_PYTHON_VERSION:-3.12}"  # cadquery-ocp supports 3.9–3.12
TARGET_TRIPLE=""
OUT_DIR="$REPO_ROOT/desktop/src-tauri/python"
MODE="build"

usage() {
  cat <<'EOF'
Usage: scripts/desktop/build-python-runtime.sh [options]

  --target <triple>   Rust target triple (default: host via `rustc --print host-tuple`).
  --python <version>  CPython version to vendor (default: 3.12; OCP supports 3.9–3.12).
  --out <dir>         Output root (default: desktop/src-tauri/python).
  --check             Validate the toolchain + inputs and print the plan; build nothing.
  -h, --help          Show this help.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --target) TARGET_TRIPLE="$2"; shift 2 ;;
    --python) PYTHON_VERSION="$2"; shift 2 ;;
    --out) OUT_DIR="$2"; shift 2 ;;
    --check) MODE="check"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

log() { printf '[build-python-runtime] %s\n' "$*"; }
die() { echo "[build-python-runtime] ERROR: $*" >&2; exit 1; }

# --- toolchain + inputs ----------------------------------------------------------
command -v uv >/dev/null 2>&1 || die "uv is required (https://docs.astral.sh/uv/). It provides python-build-standalone + relocatable venvs."
if [ -z "$TARGET_TRIPLE" ]; then
  command -v rustc >/dev/null 2>&1 || die "--target not given and rustc is unavailable to detect the host triple."
  TARGET_TRIPLE="$(rustc --print host-tuple)"
fi
[ -d "$REPO_ROOT/packages/cadpy/src/cadpy" ] || die "missing packages/cadpy source"
[ -d "$REPO_ROOT/viewer/server_py" ] || die "missing viewer/server_py"

# Per-OS venv layout. uv puts the interpreter at bin/python3 (posix) or
# Scripts/python.exe (Windows), and site-packages at lib/python3.X/site-packages
# (posix) or Lib/site-packages (Windows). Tauri's externalBin also expects a .exe
# suffix on Windows. Keep this in sync with venv_site_packages() in
# desktop/src-tauri/src/lib.rs.
case "$TARGET_TRIPLE" in
  *windows*) VENV_PY_REL="Scripts/python.exe"; BIN_EXT=".exe" ;;
  *)         VENV_PY_REL="bin/python3";        BIN_EXT="" ;;
esac

VENV_DIR="$OUT_DIR/runtime/.venv"
BIN_NAME="cad-viewer-backend-$TARGET_TRIPLE$BIN_EXT"

# Echo the venv's site-packages dir (Windows layout first, then posix).
venv_site_packages() {
  if [ -d "$1/Lib/site-packages" ]; then
    echo "$1/Lib/site-packages"
    return 0
  fi
  local d
  d="$(find "$1/lib" -maxdepth 1 -type d -name 'python3*' 2>/dev/null | head -1)"
  [ -n "$d" ] && echo "$d/site-packages"
}

log "repo:    $REPO_ROOT"
log "target:  $TARGET_TRIPLE"
log "python:  $PYTHON_VERSION"
log "out:     $OUT_DIR"
log "sidecar: binaries/$BIN_NAME"

if [ "$MODE" = "check" ]; then
  log "check OK — toolchain present, inputs resolved. (no build performed)"
  exit 0
fi

# --- build -----------------------------------------------------------------------
rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR/binaries" "$OUT_DIR/runtime"

log "installing CPython $PYTHON_VERSION (python-build-standalone via uv)"
uv python install "$PYTHON_VERSION"

log "creating relocatable venv"
uv venv --python "$PYTHON_VERSION" --relocatable "$VENV_DIR"

log "installing cadpy + OCP (novtk) + build123d into the venv"
# cadquery-ocp-novtk: OpenCASCADE without VTK (the JS frontend renders). build123d
# is the modeling API cadpy generators use. cadpy / cadpy_metadata are installed
# from the repo so the desktop engine is byte-identical to the agent's.
VIRTUAL_ENV="$VENV_DIR" uv pip install \
  "cadquery-ocp-novtk" \
  "build123d" \
  "$REPO_ROOT/packages/cadpy" \
  "$REPO_ROOT/packages/cadpy_metadata"

log "vendoring server_py (HTTP backend + warm worker)"
rsync -a --delete \
  --exclude __pycache__ --exclude '*.pyc' --exclude tests --exclude 'test_*.py' \
  "$REPO_ROOT/viewer/server_py/" "$OUT_DIR/runtime/server_py/"

if [ -d "$REPO_ROOT/viewer/dist" ]; then
  log "vendoring built SPA (viewer/dist)"
  rsync -a --delete "$REPO_ROOT/viewer/dist/" "$OUT_DIR/runtime/dist/"
else
  log "WARNING: viewer/dist not found — run the viewer build first so the SPA ships."
fi

log "placing the sidecar interpreter as binaries/$BIN_NAME"
# Tauri externalBin requires a real binary named with the target-triple suffix
# (and .exe on Windows).
[ -f "$VENV_DIR/$VENV_PY_REL" ] || die "venv interpreter not found at $VENV_DIR/$VENV_PY_REL (target $TARGET_TRIPLE)"
cp "$VENV_DIR/$VENV_PY_REL" "$OUT_DIR/binaries/$BIN_NAME"
chmod +x "$OUT_DIR/binaries/$BIN_NAME"

# RELOCATED smoke test — the critical gate. The interpreter is copied OUT of the
# venv, so importing cadpy/OCP/build123d only works if the runtime env (PYTHONHOME +
# the venv site-packages on PYTHONPATH) resolves them from a MOVED location. Running
# the test in-place would pass even when the bundled app fails on the user's machine,
# so we copy binaries/ + runtime/ to a fresh path and import there, mirroring exactly
# the env desktop/src-tauri/src/lib.rs sets at runtime.
log "relocated smoke test (simulates the bundled app on a user machine)"
SMOKE_TMP="$(mktemp -d)"
cp -R "$OUT_DIR/binaries" "$SMOKE_TMP/binaries"
cp -R "$OUT_DIR/runtime" "$SMOKE_TMP/runtime"
SMOKE_VENV="$SMOKE_TMP/runtime/.venv"
SMOKE_SITE="$(venv_site_packages "$SMOKE_VENV")"
[ -n "$SMOKE_SITE" ] || die "site-packages not found in relocated venv $SMOKE_VENV (target $TARGET_TRIPLE)"
if PYTHONHOME="$SMOKE_VENV" PYTHONPATH="$SMOKE_TMP/runtime:$SMOKE_SITE" \
     "$SMOKE_TMP/binaries/$BIN_NAME" -c 'import cadpy, OCP, build123d; print("relocated engine OK")'; then
  log "relocated smoke test PASSED"
  rm -rf "$SMOKE_TMP"
else
  log "ERROR: relocated smoke test FAILED — the bundled runtime cannot import from a moved path."
  log "The packaged desktop app would crash on launch. Inspect: $SMOKE_TMP"
  log "(env mirrors desktop/src-tauri/src/lib.rs: PYTHONHOME=<runtime>/.venv, PYTHONPATH=<runtime>:<site-packages>)"
  exit 1
fi

log "done. Sidecar: $OUT_DIR/binaries/$BIN_NAME  Resources: $OUT_DIR/runtime/"
