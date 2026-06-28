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

case "$TARGET_TRIPLE" in
  *windows*)
    die "Windows packaging is not yet ported to the self-contained-runtime + shim design (it needs the Windows cpython layout + a .bat/.exe shim). Build mac/linux for now."
    ;;
esac

# Bundle the FULL self-contained python-build-standalone interpreter (bin + lib +
# stdlib) so libpython and the stdlib resolve relative to the interpreter wherever
# the app ends up installed. A uv VENV is NOT self-contained — its python is a stub
# that references the pbs BASE install — so copying the venv binary breaks with a
# dyld "libpython not loaded" error once moved. We bundle the base install instead,
# located via the venv's pyvenv.cfg `home` (= the pbs bin dir).
PBS_BIN="$(grep -E '^home[[:space:]]*=' "$VENV_DIR/pyvenv.cfg" | head -1 | cut -d= -f2- | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
PBS_ROOT="$(dirname "$PBS_BIN")"
[ -x "$PBS_ROOT/bin/python3" ] || die "python-build-standalone install not found at $PBS_ROOT (from $VENV_DIR/pyvenv.cfg)"
log "bundling the self-contained interpreter from $PBS_ROOT"
rsync -a "$PBS_ROOT/" "$OUT_DIR/runtime/cpython/"

VENV_SITE="$(venv_site_packages "$VENV_DIR")"
[ -n "$VENV_SITE" ] || die "venv site-packages not found under $VENV_DIR"
log "bundling site-packages (cadpy + OCP + build123d)"
rsync -a "$VENV_SITE/" "$OUT_DIR/runtime/site-packages/"
rm -rf "$VENV_DIR"  # the non-self-contained venv is not shipped

# externalBin = a launcher shim. Tauri copies the externalBin into the .app's
# Contents/MacOS/, separating it from its sibling resources, so the sidecar CANNOT be
# the interpreter itself (it would lose its ../lib). The shim finds the runtime next
# to itself (smoke-test layout: ../runtime) or one level up (the .app: ../Resources/
# runtime), puts server_py + site-packages on PYTHONPATH, and execs the bundled
# interpreter — which self-resolves its libpython/stdlib.
log "writing launcher shim binaries/$BIN_NAME"
cat > "$OUT_DIR/binaries/$BIN_NAME" <<'SHIM'
#!/bin/sh
here=$(cd "$(dirname "$0")" && pwd)
for rt in "$here/../runtime" "$here/../Resources/runtime"; do
  if [ -x "$rt/cpython/bin/python3" ]; then
    PYTHONPATH="$rt:$rt/site-packages${PYTHONPATH:+:$PYTHONPATH}"
    export PYTHONPATH
    exec "$rt/cpython/bin/python3" "$@"
  fi
done
echo "cad-viewer-backend: bundled python runtime not found near $here" >&2
exit 1
SHIM
chmod +x "$OUT_DIR/binaries/$BIN_NAME"

# RELOCATED smoke test — the critical gate. Copy binaries/ + runtime/ to a fresh path
# and import there THROUGH THE SHIM, exactly as the bundled .app would. Fails the
# build (not the user's launch) if the relocated runtime cannot import.
log "relocated smoke test (simulates the bundled app on a user machine)"
SMOKE_TMP="$(mktemp -d)"
cp -R "$OUT_DIR/binaries" "$SMOKE_TMP/binaries"
cp -R "$OUT_DIR/runtime" "$SMOKE_TMP/runtime"
if "$SMOKE_TMP/binaries/$BIN_NAME" -c 'import cadpy, OCP, build123d; print("relocated engine OK")'; then
  log "relocated smoke test PASSED"
  rm -rf "$SMOKE_TMP"
else
  log "ERROR: relocated smoke test FAILED — the bundled runtime cannot import from a moved path."
  log "The packaged desktop app would crash on launch. Inspect: $SMOKE_TMP"
  exit 1
fi

log "done. Sidecar shim: $OUT_DIR/binaries/$BIN_NAME"
log "Runtime: $OUT_DIR/runtime/ (cpython + site-packages + server_py + dist)"
