#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

MODE="write"
CLEAN=0

IMPLICITJS_PACKAGE_DIR="$REPO_ROOT/packages/implicitjs"
IMPLICITJS_RUNTIME_DIR="${IMPLICITJS_RUNTIME_DIR:-$REPO_ROOT/skills/implicit-cad/scripts/packages/implicitjs}"
RUNTIME_NODE_MODULES_SOURCE="${IMPLICITJS_RUNTIME_NODE_MODULES_SOURCE:-}"
CHECK_DIR="${IMPLICIT_CAD_SKILL_BUNDLE_CHECK_DIR:-$REPO_ROOT/tmp/implicit-cad-skill-runtime-check}"

usage() {
  cat <<'EOF'
Usage:
  scripts/bundle/bundle-skill.sh implicit-cad [--check] [--clean]

Bundles the implicitjs package copy used by skills/implicit-cad in production
layouts.

Options:
  --check  Bundle into tmp/ and fail if checked-in production outputs are stale.
  --clean  Remove temporary check directories first.
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

require_file() {
  local path_to_check="$1"
  local label="$2"
  if [ ! -f "$path_to_check" ]; then
    echo "Missing $label: $path_to_check" >&2
    exit 1
  fi
}

require_dir() {
  local path_to_check="$1"
  local label="$2"
  if [ ! -d "$path_to_check" ]; then
    echo "Missing $label: $path_to_check" >&2
    exit 1
  fi
}

sync_implicitjs_package() {
  local target_dir="$1"
  rm -rf "$target_dir"
  mkdir -p "$target_dir"
  rsync -a --delete \
    --prune-empty-dirs \
    --delete-excluded \
    --exclude node_modules \
    --exclude dist \
    --exclude coverage \
    --exclude tmp \
    --exclude .vite \
    --exclude .DS_Store \
    "$IMPLICITJS_PACKAGE_DIR/" "$target_dir/"
}

validate_runtime_node_modules_source() {
  local dependency
  [ -n "$RUNTIME_NODE_MODULES_SOURCE" ] || return 0
  for dependency in playwright playwright-core three gifenc; do
    require_dir \
      "$RUNTIME_NODE_MODULES_SOURCE/$dependency" \
      "implicit CAD runtime dependency $dependency"
  done
}

sync_runtime_node_modules() {
  local target_dir="$1"
  local dependency stage_dir
  [ -n "$RUNTIME_NODE_MODULES_SOURCE" ] || return 0
  stage_dir="${target_dir}.node_modules-stage.$$"
  rm -rf "$stage_dir"
  mkdir -p "$stage_dir"
  for dependency in playwright playwright-core three gifenc; do
    if ! rsync -a \
      "$RUNTIME_NODE_MODULES_SOURCE/$dependency/" \
      "$stage_dir/$dependency/"; then
      rm -rf "$stage_dir"
      return 1
    fi
  done
  rm -rf "$target_dir/node_modules"
  mv "$stage_dir" "$target_dir/node_modules"
}

check_implicitjs_package() {
  local expected_dir="$CHECK_DIR/packages/implicitjs"
  local label="${IMPLICITJS_RUNTIME_DIR#$REPO_ROOT/}"
  local diff_path="${TMPDIR:-/tmp}/implicit-cad-skill-implicitjs-package-diff.txt"
  if [ ! -d "$IMPLICITJS_RUNTIME_DIR" ]; then
    echo "Missing generated implicitjs package runtime: $label" >&2
    return 1
  fi
  if ! diff -qr \
    -x node_modules \
    -x dist \
    -x coverage \
    -x tmp \
    -x .vite \
    -x .DS_Store \
    "$expected_dir" "$IMPLICITJS_RUNTIME_DIR" >"$diff_path"; then
    cat "$diff_path" >&2
    echo "" >&2
    echo "Implicit CAD skill implicitjs package runtime is stale." >&2
    return 1
  fi
  return 0
}

check_development_layout() {
  "$REPO_ROOT/scripts/dev/setup-skill-symlink.sh" implicit-cad --check
  echo "Implicit CAD skill is in development symlink layout; production package freshness is checked on build-test/main."
}

require_file "$IMPLICITJS_PACKAGE_DIR/package.json" "implicitjs package"
require_dir "$IMPLICITJS_PACKAGE_DIR/src" "implicitjs source"
require_file "$IMPLICITJS_PACKAGE_DIR/scripts/snapshot.mjs" "implicit CAD snapshot CLI"
require_file "$IMPLICITJS_PACKAGE_DIR/scripts/export.mjs" "implicit CAD export CLI"
require_file "$IMPLICITJS_PACKAGE_DIR/scripts/canonical-build.mjs" "implicit CAD canonical build CLI"
require_file "$IMPLICITJS_PACKAGE_DIR/src/lib/implicitCad/canonicalBuildWorker.mjs" "implicit CAD restricted build worker"
require_file "$REPO_ROOT/skills/implicit-cad/scripts/canonical-build.mjs" "implicit CAD skill canonical build entry"

if [ "$MODE" = "check" ] && [ -L "$IMPLICITJS_RUNTIME_DIR" ]; then
  check_development_layout
  exit 0
fi

if [ "$CLEAN" -eq 1 ]; then
  rm -rf "$CHECK_DIR"
fi

if [ "$MODE" = "check" ]; then
  rm -rf "$CHECK_DIR"
  sync_implicitjs_package "$CHECK_DIR/packages/implicitjs"

  stale=0
  check_implicitjs_package || stale=1

  if [ "$stale" -ne 0 ]; then
    echo "" >&2
    echo "Run scripts/bundle/bundle-skill.sh implicit-cad and commit the updated production package copy." >&2
    exit 1
  fi
  echo "Implicit CAD skill production outputs are up to date."
else
  validate_runtime_node_modules_source
  sync_implicitjs_package "$IMPLICITJS_RUNTIME_DIR"
  sync_runtime_node_modules "$IMPLICITJS_RUNTIME_DIR"
  echo "Bundled skills/implicit-cad/scripts/packages/implicitjs"
fi
