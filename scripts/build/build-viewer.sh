#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

MODE="write"
CLEAN=0

CADJS_PACKAGE_DIR="$REPO_ROOT/packages/cadjs"
CADPY_PACKAGE_DIR="$REPO_ROOT/packages/cadpy"
VIEWER_CADJS_DIR="$REPO_ROOT/viewer/packages/cadjs"
VIEWER_CADPY_DIR="$REPO_ROOT/viewer/packages/cadpy"

usage() {
  cat <<'EOF'
Usage:
  scripts/build/build-viewer.sh [--check] [--clean]

Builds generated, viewer-local package copies used by the root Viewer, hosted
viewer deployments, and the packaged cad-viewer skill runtime.

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

if [ ! -f "$CADJS_PACKAGE_DIR/package.json" ] || [ ! -d "$CADJS_PACKAGE_DIR/src" ]; then
  echo "Missing cadjs package source: $CADJS_PACKAGE_DIR" >&2
  exit 1
fi

if [ ! -f "$CADPY_PACKAGE_DIR/pyproject.toml" ] || [ ! -d "$CADPY_PACKAGE_DIR/src/cadpy" ]; then
  echo "Missing cadpy package source: $CADPY_PACKAGE_DIR" >&2
  exit 1
fi

if ! command -v rsync >/dev/null 2>&1; then
  echo "rsync is required to build the Viewer package runtimes." >&2
  exit 1
fi

sync_cadjs_package() {
  mkdir -p "$VIEWER_CADJS_DIR"
  rsync -a --delete \
    --delete-excluded \
    --exclude node_modules \
    --exclude dist \
    --exclude coverage \
    --exclude tmp \
    --exclude .vite \
    --exclude .DS_Store \
    "$CADJS_PACKAGE_DIR/" "$VIEWER_CADJS_DIR/"
}

sync_cadpy_package() {
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

check_cadjs_package() {
  local label="${VIEWER_CADJS_DIR#$REPO_ROOT/}"
  local diff_path="${TMPDIR:-/tmp}/viewer-cadjs-package-diff.txt"
  if [ ! -d "$VIEWER_CADJS_DIR" ]; then
    echo "Missing generated viewer cadjs package: $label" >&2
    echo "Run scripts/build/build-viewer.sh and commit the generated copy." >&2
    exit 1
  fi
  if ! diff -qr \
    -x node_modules \
    -x dist \
    -x coverage \
    -x tmp \
    -x .vite \
    -x .DS_Store \
    "$CADJS_PACKAGE_DIR" "$VIEWER_CADJS_DIR" >"$diff_path"; then
    cat "$diff_path" >&2
    echo "" >&2
    echo "Viewer cadjs package is stale." >&2
    echo "Run scripts/build/build-viewer.sh and commit viewer/packages/cadjs." >&2
    exit 1
  fi
  echo "$label is up to date."
}

check_cadpy_package() {
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
  check_cadjs_package
  check_cadpy_package
else
  if [ "$CLEAN" -eq 1 ]; then
    rm -rf "$VIEWER_CADJS_DIR"
    rm -rf "$VIEWER_CADPY_DIR"
  fi
  sync_cadjs_package
  sync_cadpy_package "$CADPY_PACKAGE_DIR" "$VIEWER_CADPY_DIR"
  echo "Built ${VIEWER_CADJS_DIR#$REPO_ROOT/}"
  echo "Built ${VIEWER_CADPY_DIR#$REPO_ROOT/}"
fi
