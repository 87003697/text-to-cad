#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

MODE="write"
CLEAN=0

IMPLICITJS_PACKAGE_DIR="$REPO_ROOT/packages/implicitjs"
SNAPSHOT_ENTRYPOINT="$REPO_ROOT/packages/implicitjs/src/common/implicitHeadlessRenderEntry.js"
IMPLICITJS_RUNTIME_DIR="$REPO_ROOT/skills/implicit-cad/scripts/packages/implicitjs"
SNAPSHOT_RUNTIME_DIR="$REPO_ROOT/skills/implicit-cad/scripts/snapshot/runtime"
CHECK_DIR="${IMPLICIT_CAD_SKILL_BUNDLE_CHECK_DIR:-$REPO_ROOT/tmp/implicit-cad-skill-runtime-check}"
ESBUILD_BIN="$REPO_ROOT/viewer/node_modules/.bin/esbuild"

usage() {
  cat <<'EOF'
Usage:
  scripts/bundle/bundle-skill.sh implicit-cad [--check] [--clean]

Bundles the package copy and self-contained snapshot runtime used by
skills/implicit-cad in production layouts.

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

write_render_html() {
  local target_dir="$1"
  cat > "$target_dir/render.html" <<'EOF'
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>Implicit CAD snapshot render</title>
    <style>
      html,
      body {
        margin: 0;
        min-width: 100%;
        min-height: 100%;
        overflow: hidden;
        background: transparent;
      }
    </style>
  </head>
  <body>
    <script type="module" src="/snapshot-render.js"></script>
  </body>
</html>
EOF
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

build_snapshot_runtime() {
  local target_dir="$1"
  rm -rf "$target_dir"
  mkdir -p "$target_dir"
  write_render_html "$target_dir"
  "$ESBUILD_BIN" "$SNAPSHOT_ENTRYPOINT" \
    --bundle \
    --format=esm \
    --platform=browser \
    --target=es2020 \
    --alias:three="$REPO_ROOT/viewer/node_modules/three/build/three.module.js" \
    --alias:gifenc="$REPO_ROOT/viewer/node_modules/gifenc/dist/gifenc.esm.js" \
    --main-fields=module,main \
    --minify \
    --legal-comments=none \
    --outfile="$target_dir/snapshot-render.js"
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

check_runtime_file() {
  local expected="$1"
  local actual="$2"
  local label="$3"
  if [ ! -f "$actual" ]; then
    echo "Missing generated implicit CAD runtime file: $label" >&2
    return 1
  fi
  if ! cmp -s "$expected" "$actual"; then
    echo "Stale generated implicit CAD runtime file: $label" >&2
    return 1
  fi
  return 0
}

check_development_layout() {
  "$REPO_ROOT/scripts/dev/setup-skill-symlink.sh" implicit-cad --check
  echo "Implicit CAD skill is in development symlink layout; production runtime freshness is checked on build-test/main."
}

require_file "$IMPLICITJS_PACKAGE_DIR/package.json" "implicitjs package"
require_dir "$IMPLICITJS_PACKAGE_DIR/src" "implicitjs source"
require_file "$SNAPSHOT_ENTRYPOINT" "implicit CAD snapshot entrypoint"

if [ "$MODE" = "check" ] && [ -L "$IMPLICITJS_RUNTIME_DIR" ]; then
  check_development_layout
  exit 0
fi

require_file "$ESBUILD_BIN" "viewer esbuild binary; run npm install --prefix viewer"

if [ "$CLEAN" -eq 1 ]; then
  rm -rf "$CHECK_DIR"
fi

if [ "$MODE" = "check" ]; then
  rm -rf "$CHECK_DIR"
  sync_implicitjs_package "$CHECK_DIR/packages/implicitjs"
  build_snapshot_runtime "$CHECK_DIR/runtime/snapshot"

  stale=0
  check_implicitjs_package || stale=1
  check_runtime_file \
    "$CHECK_DIR/runtime/snapshot/render.html" \
    "$SNAPSHOT_RUNTIME_DIR/render.html" \
    "skills/implicit-cad/scripts/snapshot/runtime/render.html" || stale=1
  check_runtime_file \
    "$CHECK_DIR/runtime/snapshot/snapshot-render.js" \
    "$SNAPSHOT_RUNTIME_DIR/snapshot-render.js" \
    "skills/implicit-cad/scripts/snapshot/runtime/snapshot-render.js" || stale=1

  if [ "$stale" -ne 0 ]; then
    echo "" >&2
    echo "Run scripts/bundle/bundle-skill.sh implicit-cad and commit the updated production outputs." >&2
    exit 1
  fi
  echo "Implicit CAD skill production outputs are up to date."
else
  sync_implicitjs_package "$IMPLICITJS_RUNTIME_DIR"
  build_snapshot_runtime "$SNAPSHOT_RUNTIME_DIR"
  echo "Bundled skills/implicit-cad/scripts/packages/implicitjs"
  echo "Bundled skills/implicit-cad/scripts/snapshot/runtime"
fi
