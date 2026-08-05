#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

MODE="write"
CLEAN=0

PACKAGE_DIR="$REPO_ROOT/packages/meshscope"
RUNTIME_DIR="$REPO_ROOT/skills/mesh-compare/scripts/packages/meshscope"
CHECK_DIR="${MESH_COMPARE_SKILL_CHECK_DIR:-$REPO_ROOT/tmp/mesh-compare-skill-runtime-check}"

usage() {
  cat <<'EOF'
Usage:
  scripts/bundle/bundle-skill.sh mesh-compare [--check] [--clean]

Vendors packages/meshscope into the mesh-compare production skill runtime.

Options:
  --check  Fail if the generated runtime copy is stale.
  --clean  Remove the temporary check directory first.
  -h, --help
           Show this help.
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

if [ ! -f "$PACKAGE_DIR/pyproject.toml" ] || [ ! -d "$PACKAGE_DIR/src/meshscope" ]; then
  echo "Missing meshscope package source: $PACKAGE_DIR" >&2
  exit 1
fi

if ! command -v rsync >/dev/null 2>&1; then
  echo "rsync is required to vendor meshscope into mesh-compare." >&2
  exit 1
fi

sync_runtime() {
  local target_dir="$1"
  rm -rf "$target_dir"
  mkdir -p "$target_dir"
  rsync -a --delete \
    --delete-excluded \
    --exclude __pycache__ \
    --exclude .pytest_cache \
    --exclude '*.pyc' \
    --exclude '*.egg-info' \
    --exclude build \
    --exclude dist \
    --exclude tests \
    --exclude __tests__ \
    "$PACKAGE_DIR/" "$target_dir/"
}

check_runtime() {
  local expected_dir="$CHECK_DIR/packages/meshscope"
  if [ ! -d "$RUNTIME_DIR" ]; then
    echo "Missing generated meshscope runtime: skills/mesh-compare/scripts/packages/meshscope" >&2
    return 1
  fi
  if ! diff -qr \
    -x __pycache__ \
    -x .pytest_cache \
    -x '*.pyc' \
    -x '*.egg-info' \
    -x build \
    -x dist \
    -x tests \
    -x __tests__ \
    "$expected_dir" "$RUNTIME_DIR" >"${TMPDIR:-/tmp}/mesh-compare-meshscope-runtime-diff.txt"; then
    cat "${TMPDIR:-/tmp}/mesh-compare-meshscope-runtime-diff.txt" >&2
    echo "mesh-compare meshscope runtime is stale." >&2
    return 1
  fi
}

if [ "$MODE" = "check" ] && [ -L "$RUNTIME_DIR" ]; then
  "$REPO_ROOT/scripts/dev/setup-skill-symlink.sh" mesh-compare --check
  echo "mesh-compare is in development symlink layout; production runtime is checked on build-test/main."
  exit 0
fi

if [ "$CLEAN" -eq 1 ]; then
  rm -rf "$CHECK_DIR"
fi

if [ "$MODE" = "check" ]; then
  rm -rf "$CHECK_DIR"
  sync_runtime "$CHECK_DIR/packages/meshscope"
  check_runtime || {
    echo "Run scripts/bundle/bundle-skill.sh mesh-compare and commit the production runtime." >&2
    exit 1
  }
  echo "mesh-compare meshscope runtime is up to date."
else
  sync_runtime "$RUNTIME_DIR"
  echo "Bundled skills/mesh-compare/scripts/packages/meshscope"
fi
