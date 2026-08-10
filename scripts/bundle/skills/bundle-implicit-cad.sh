#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
export BUNDLE_REPO_ROOT="$REPO_ROOT"
# shellcheck source=../lib/vendor.sh
source "$SCRIPT_DIR/../lib/vendor.sh"
# shellcheck source=../lib/node_builders.sh
source "$SCRIPT_DIR/../lib/node_builders.sh"

MODE="write"
CLEAN=0
PRINT_OUTPUTS=0

IMPLICITJS_PACKAGE_DIR="$REPO_ROOT/packages/implicitjs"
IMPLICITJS_RUNTIME_DIR="$REPO_ROOT/skills/implicit-cad/scripts/packages/implicitjs"
# scripts/gen drives cadgen.implicit_artifact, so the skill vendors the Python package the
# same way `cad` and `dxf` do.
CADGEN_PACKAGE_DIR="$REPO_ROOT/packages/cadgen"
CADGEN_RUNTIME_DIR="$REPO_ROOT/skills/implicit-cad/scripts/packages/cadgen"
# The Node BUILDER cadgen spawns. It lives in packages/cadjs and imports meshoptimizer and
# implicitjs, so it is esbuilt self-contained rather than copied (design §4.5). Two of its
# three entries exist only because their runtime path is computed from `import.meta.url`
# inside the bundle and therefore cannot be inlined: implicitClosureHooks.mjs is
# `register()`-ed by name, and meshWorkerEntry.js is the worker_threads entry
# implicitjs/lib/implicitCad/meshWorkers.js spawns.
BUILDERS_RUNTIME_DIR="$REPO_ROOT/skills/implicit-cad/scripts/packages/cadjs/bin"
BUILDER_ENTRIES=(
  "$REPO_ROOT/packages/cadjs/bin/implicit-artifact.mjs"
  "$REPO_ROOT/packages/cadjs/bin/implicitClosureHooks.mjs"
  "$REPO_ROOT/packages/implicitjs/src/lib/implicitCad/meshWorkerEntry.js"
)
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
  --print-outputs
           Print the repo-relative generated output paths, then exit.
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
    --print-outputs)
      PRINT_OUTPUTS=1
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

if [ "$PRINT_OUTPUTS" -eq 1 ]; then
  printf '%s\n' "${IMPLICITJS_RUNTIME_DIR#"$REPO_ROOT"/}"
  printf '%s\n' "${CADGEN_RUNTIME_DIR#"$REPO_ROOT"/}"
  printf '%s\n' "${BUILDERS_RUNTIME_DIR#"$REPO_ROOT"/}"
  exit 0
fi

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
require_python_package "$CADGEN_PACKAGE_DIR" cadgen
ensure_node_builder_deps

if [ "$CLEAN" -eq 1 ]; then
  rm -rf "$CHECK_DIR"
fi

# The builder bundles are esbuild output, never a symlink, so they are checked in BOTH
# layouts -- unlike the vendored package copies below, which the development layout
# deliberately replaces with links to their sources.
check_builders() {
  check_node_builders \
    "$BUILDERS_RUNTIME_DIR" "$CHECK_DIR/packages/cadjs/bin" \
    "skills/implicit-cad/scripts/packages/cadjs/bin" \
    "Run scripts/bundle/bundle-skill.sh implicit-cad and commit skills/implicit-cad/scripts/packages/cadjs." \
    "${BUILDER_ENTRIES[@]}"
}

if [ "$MODE" = "check" ] && [ -L "$IMPLICITJS_RUNTIME_DIR" ]; then
  rm -rf "$CHECK_DIR"
  check_builders || exit 1
  check_development_layout
  exit 0
fi

if [ "$MODE" = "check" ]; then
  rm -rf "$CHECK_DIR"
  sync_implicitjs_package "$CHECK_DIR/packages/implicitjs"

  stale=0
  check_implicitjs_package || stale=1
  check_builders || stale=1
  check_python_runtime \
    "$CADGEN_PACKAGE_DIR" "$CADGEN_RUNTIME_DIR" "$CHECK_DIR/packages/cadgen" \
    "skills/implicit-cad/scripts/packages/cadgen" \
    "Run scripts/bundle/bundle-skill.sh implicit-cad and commit skills/implicit-cad/scripts/packages/cadgen." \
    || stale=1

  if [ "$stale" -ne 0 ]; then
    echo "" >&2
    echo "Run scripts/bundle/bundle-skill.sh implicit-cad and commit the updated production package copy." >&2
    exit 1
  fi
  echo "Implicit CAD skill production outputs are up to date."
else
  sync_implicitjs_package "$IMPLICITJS_RUNTIME_DIR"
  echo "Bundled skills/implicit-cad/scripts/packages/implicitjs"
  vendor_python_package "$CADGEN_PACKAGE_DIR" "$CADGEN_RUNTIME_DIR"
  echo "Bundled skills/implicit-cad/scripts/packages/cadgen"
  bundle_node_builders "$BUILDERS_RUNTIME_DIR" "${BUILDER_ENTRIES[@]}"
  echo "Bundled skills/implicit-cad/scripts/packages/cadjs/bin"
fi
