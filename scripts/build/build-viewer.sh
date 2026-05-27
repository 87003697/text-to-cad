#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

MODE="write"
CLEAN=0

CADPY_PACKAGE_DIR="$REPO_ROOT/packages/cadpy"
VIEWER_CADPY_DIR="$REPO_ROOT/viewer/packages/cadpy"

usage() {
  cat <<'EOF'
Usage:
  scripts/build/build-viewer.sh [--check] [--clean]

Builds generated, viewer-local Python package copies used by the root Viewer
and by the packaged cad-viewer skill runtime.

Options:
  --check     Fail if viewer/packages is stale.
  --clean     Remove generated viewer package copies before writing.
  -h, --help  Show this help.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --check)
      MODE="check"
      ;;
    --clean)
      CLEAN=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if [ ! -f "$CADPY_PACKAGE_DIR/pyproject.toml" ] || [ ! -d "$CADPY_PACKAGE_DIR/src/cadpy" ]; then
  echo "Missing cadpy package source: $CADPY_PACKAGE_DIR" >&2
  exit 1
fi

if ! command -v rsync >/dev/null 2>&1; then
  echo "rsync is required to build the Viewer Python package runtime." >&2
  exit 1
fi

sync_package() {
  local source_dir="$1"
  local target_dir="$2"
  mkdir -p "$target_dir"
  rsync -a --delete \
    --delete-excluded \
    --exclude __pycache__ \
    --exclude .pytest_cache \
    --exclude '*.pyc' \
    --exclude '*.egg-info' \
    --exclude '*.md' \
    --exclude build \
    --exclude dist \
    --exclude tests \
    "$source_dir/" "$target_dir/"
}

check_package() {
  local label="${VIEWER_CADPY_DIR#$REPO_ROOT/}"
  local diff_path="${TMPDIR:-/tmp}/viewer-cadpy-package-diff.txt"
  if [ ! -d "$VIEWER_CADPY_DIR" ]; then
    echo "Missing generated viewer cadpy package: $label" >&2
    echo "Run scripts/build/build-viewer.sh and commit the generated copy." >&2
    exit 1
  fi
  if ! diff -qr \
    -x __pycache__ \
    -x .pytest_cache \
    -x '*.pyc' \
    -x '*.egg-info' \
    -x '*.md' \
    -x build \
    -x dist \
    -x tests \
    "$CADPY_PACKAGE_DIR" "$VIEWER_CADPY_DIR" >"$diff_path"; then
    cat "$diff_path" >&2
    echo "" >&2
    echo "Viewer cadpy package is stale." >&2
    echo "Run scripts/build/build-viewer.sh and commit viewer/packages/cadpy." >&2
    exit 1
  fi
  echo "$label is up to date."
}

if [ "$MODE" = "check" ]; then
  check_package
else
  if [ "$CLEAN" -eq 1 ]; then
    rm -rf "$VIEWER_CADPY_DIR"
  fi
  sync_package "$CADPY_PACKAGE_DIR" "$VIEWER_CADPY_DIR"
  echo "Built ${VIEWER_CADPY_DIR#$REPO_ROOT/}"
fi
