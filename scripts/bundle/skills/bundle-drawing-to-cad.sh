#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

MODE="write"
CLEAN=0

CADPY_PACKAGE_DIR="$REPO_ROOT/packages/cadpy"
CADPY_RUNTIME_DIR="$REPO_ROOT/skills/drawing-to-cad/scripts/packages/cadpy"
CHECK_DIR="${DRAWING_TO_CAD_CHECK_DIR:-$REPO_ROOT/tmp/drawing-to-cad-runtime-check}"

usage() {
  cat <<'EOF'
Usage:
  scripts/bundle/bundle-skill.sh drawing-to-cad [--check] [--clean]

Bundles the Python package runtime used by skills/drawing-to-cad/scripts.

Options:
  --check     Bundle into tmp/ and fail if checked outputs are stale.
  --clean     Remove temporary check directories first.
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

ensure_deps() {
  if ! command -v rsync >/dev/null 2>&1; then
    echo "rsync is required to build the Drawing to CAD skill Python runtime." >&2
    exit 1
  fi
  if [ ! -f "$CADPY_PACKAGE_DIR/pyproject.toml" ] || [ ! -d "$CADPY_PACKAGE_DIR/src/cadpy" ]; then
    echo "Missing cadpy package source: $CADPY_PACKAGE_DIR" >&2
    exit 1
  fi
}

sync_cadpy_runtime() {
  local target_dir="$1"
  rm -rf "$target_dir"
  mkdir -p "$target_dir"
  rsync -a --delete \
    --delete-excluded \
    --exclude __pycache__ \
    --exclude .pytest_cache \
    --exclude '*.pyc' \
    --exclude '*.md' \
    --exclude build \
    --exclude dist \
    --exclude '*.egg-info' \
    --exclude tests \
    "$CADPY_PACKAGE_DIR/" "$target_dir/"
}

check_cadpy_runtime() {
  local check_dir="$CHECK_DIR/packages/cadpy"
  if [ ! -d "$CADPY_RUNTIME_DIR" ]; then
    echo "Missing generated cadpy runtime: skills/drawing-to-cad/scripts/packages/cadpy" >&2
    echo "Run scripts/bundle/bundle-skill.sh drawing-to-cad and commit the updated runtime files." >&2
    exit 1
  fi
  if ! diff -qr \
    -x __pycache__ \
    -x .pytest_cache \
    -x '*.pyc' \
    -x '*.egg-info' \
    -x '*.md' \
    -x tests \
    "$check_dir" "$CADPY_RUNTIME_DIR" >/tmp/drawing-to-cad-cadpy-runtime-diff.txt; then
    cat /tmp/drawing-to-cad-cadpy-runtime-diff.txt >&2
    echo "" >&2
    echo "Drawing to CAD skill cadpy runtime is stale." >&2
    echo "Run scripts/bundle/bundle-skill.sh drawing-to-cad and commit skills/drawing-to-cad/scripts/packages/cadpy." >&2
    exit 1
  fi
  echo "Drawing to CAD skill cadpy runtime is up to date."
}

ensure_deps

if [ "$CLEAN" -eq 1 ]; then
  rm -rf "$CHECK_DIR"
fi

if [ "$MODE" = "check" ]; then
  sync_cadpy_runtime "$CHECK_DIR/packages/cadpy"
  check_cadpy_runtime
else
  sync_cadpy_runtime "$CADPY_RUNTIME_DIR"
  echo "Bundled skills/drawing-to-cad/scripts/packages/cadpy"
fi
