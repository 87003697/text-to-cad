#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

MODE="write"
CLEAN=0

MESHSCOPE_PACKAGE_DIR="$REPO_ROOT/packages/meshscope"
MESHSHOT_PACKAGE_DIR="$REPO_ROOT/packages/meshshot"
MESHSCOPE_RUNTIME_DIR="$REPO_ROOT/skills/mesh-compare/scripts/packages/meshscope"
MESHSHOT_RUNTIME_DIR="$REPO_ROOT/skills/mesh-compare/scripts/packages/meshshot"
MESHSHOT_BROWSER_ENTRYPOINT="$MESHSHOT_PACKAGE_DIR/browser/residualRenderEntry.js"
MESHSHOT_BROWSER_RUNTIME_DIR="$MESHSHOT_PACKAGE_DIR/src/meshshot/runtime"
MESHSHOT_BROWSER_RUNTIME="$MESHSHOT_BROWSER_RUNTIME_DIR/residual-render.js"
MESHSHOT_ESBUILD="$MESHSHOT_PACKAGE_DIR/node_modules/.bin/esbuild"

CHECK_DIR="${MESH_COMPARE_SKILL_CHECK_DIR:-$REPO_ROOT/tmp/mesh-compare-skill-runtime-check}"

usage() {
  cat <<'EOF'
Usage:
  scripts/bundle/bundle-skill.sh mesh-compare [--check] [--clean]

Builds the meshshot browser runtime and vendors meshscope plus meshshot into
the mesh-compare production skill runtime.

Options:
  --check  Fail if a generated browser/runtime copy is stale.
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

if [ ! -f "$MESHSCOPE_PACKAGE_DIR/pyproject.toml" ] || [ ! -d "$MESHSCOPE_PACKAGE_DIR/src/meshscope" ]; then
  echo "Missing meshscope package source: $MESHSCOPE_PACKAGE_DIR" >&2
  exit 1
fi
if [ ! -f "$MESHSHOT_PACKAGE_DIR/pyproject.toml" ] || [ ! -d "$MESHSHOT_PACKAGE_DIR/src/meshshot" ]; then
  echo "Missing meshshot package source: $MESHSHOT_PACKAGE_DIR" >&2
  exit 1
fi
if [ ! -f "$MESHSHOT_BROWSER_ENTRYPOINT" ]; then
  echo "Missing meshshot browser entrypoint: $MESHSHOT_BROWSER_ENTRYPOINT" >&2
  exit 1
fi
if [ ! -f "$MESHSHOT_BROWSER_RUNTIME_DIR/render.html" ]; then
  echo "Missing meshshot render page: $MESHSHOT_BROWSER_RUNTIME_DIR/render.html" >&2
  exit 1
fi
if ! command -v rsync >/dev/null 2>&1; then
  echo "rsync is required to vendor mesh-compare shared packages." >&2
  exit 1
fi
if ! command -v npm >/dev/null 2>&1 || ! command -v node >/dev/null 2>&1; then
  echo "npm and node are required to build the meshshot browser runtime." >&2
  exit 1
fi

ensure_build_deps() {
  if [ ! -x "$MESHSHOT_ESBUILD" ] || ! npm ls \
    --prefix "$MESHSHOT_PACKAGE_DIR" \
    --depth=0 \
    --silent >/dev/null 2>&1; then
    npm ci --prefix "$MESHSHOT_PACKAGE_DIR" \
      --ignore-scripts \
      --no-audit \
      --no-fund \
      --fetch-retries=1 \
      --fetch-timeout=10000
  fi
}

build_meshshot_runtime() {
  local target="$1"
  mkdir -p "$(dirname "$target")"
  "$MESHSHOT_ESBUILD" "$MESHSHOT_BROWSER_ENTRYPOINT" \
    --bundle \
    --format=esm \
    --platform=browser \
    --target=es2022 \
    --main-fields=module,main \
    --minify \
    --legal-comments=none \
    --alias:three="$MESHSHOT_PACKAGE_DIR/node_modules/three" \
    --outfile="$target"
  node -e 'const fs=require("fs");const path=process.argv[1];const text=fs.readFileSync(path,"utf8").replace(/[ \t]+$/gm,"");fs.writeFileSync(path,text);' "$target"
}

sync_package() {
  local source_dir="$1"
  local target_dir="$2"
  rm -rf "$target_dir"
  mkdir -p "$target_dir"
  rsync -a --delete \
    --delete-excluded \
    --exclude __pycache__ \
    --exclude .pytest_cache \
    --exclude '*.pyc' \
    --exclude '*.egg-info' \
    --exclude '*.so' \
    --exclude '*.dylib' \
    --exclude '*.pyd' \
    --exclude node_modules \
    --exclude build \
    --exclude dist \
    --exclude tests \
    --exclude __tests__ \
    "$source_dir/" "$target_dir/"
}

check_package() {
  local expected_dir="$1"
  local actual_dir="$2"
  local label="$3"
  local diff_path="$4"
  if [ ! -d "$actual_dir" ]; then
    echo "Missing generated $label runtime: ${actual_dir#"$REPO_ROOT"/}" >&2
    return 1
  fi
  if ! diff -qr \
    -x __pycache__ \
    -x .pytest_cache \
    -x '*.pyc' \
    -x '*.egg-info' \
    -x '*.so' \
    -x '*.dylib' \
    -x '*.pyd' \
    -x node_modules \
    -x build \
    -x dist \
    -x tests \
    -x __tests__ \
    "$expected_dir" "$actual_dir" >"$diff_path"; then
    cat "$diff_path" >&2
    echo "mesh-compare $label runtime is stale." >&2
    return 1
  fi
}

ensure_build_deps

if [ "$CLEAN" -eq 1 ]; then
  rm -rf "$CHECK_DIR"
fi

if [ "$MODE" = "check" ]; then
  rm -rf "$CHECK_DIR"
  build_meshshot_runtime "$CHECK_DIR/residual-render.js"
  if ! cmp -s "$CHECK_DIR/residual-render.js" "$MESHSHOT_BROWSER_RUNTIME"; then
    echo "meshshot browser runtime is stale." >&2
    echo "Run scripts/bundle/bundle-skill.sh mesh-compare and commit packages/meshshot/src/meshshot/runtime." >&2
    exit 1
  fi
  if [ -L "$MESHSCOPE_RUNTIME_DIR" ] || [ -L "$MESHSHOT_RUNTIME_DIR" ]; then
    "$REPO_ROOT/scripts/dev/setup-skill-symlink.sh" mesh-compare --check
    echo "mesh-compare is in development symlink layout; shared runtimes and meshshot browser bundle are current."
    exit 0
  fi
  sync_package "$MESHSCOPE_PACKAGE_DIR" "$CHECK_DIR/packages/meshscope"
  sync_package "$MESHSHOT_PACKAGE_DIR" "$CHECK_DIR/packages/meshshot"
  stale=0
  check_package \
    "$CHECK_DIR/packages/meshscope" \
    "$MESHSCOPE_RUNTIME_DIR" \
    "meshscope" \
    "${TMPDIR:-/tmp}/mesh-compare-meshscope-runtime-diff.txt" || stale=1
  check_package \
    "$CHECK_DIR/packages/meshshot" \
    "$MESHSHOT_RUNTIME_DIR" \
    "meshshot" \
    "${TMPDIR:-/tmp}/mesh-compare-meshshot-runtime-diff.txt" || stale=1
  if [ "$stale" -ne 0 ]; then
    echo "Run scripts/bundle/bundle-skill.sh mesh-compare and commit the production runtime." >&2
    exit 1
  fi
  echo "mesh-compare shared runtimes are up to date."
  exit 0
fi

build_meshshot_runtime "$MESHSHOT_BROWSER_RUNTIME"
if [ -L "$MESHSCOPE_RUNTIME_DIR" ] || [ -L "$MESHSHOT_RUNTIME_DIR" ]; then
  "$REPO_ROOT/scripts/dev/setup-skill-symlink.sh" mesh-compare --check
  echo "Built packages/meshshot/src/meshshot/runtime/residual-render.js"
  echo "mesh-compare development symlinks already expose shared package sources."
  exit 0
fi
sync_package "$MESHSCOPE_PACKAGE_DIR" "$MESHSCOPE_RUNTIME_DIR"
sync_package "$MESHSHOT_PACKAGE_DIR" "$MESHSHOT_RUNTIME_DIR"
echo "Built packages/meshshot/src/meshshot/runtime/residual-render.js"
echo "Bundled skills/mesh-compare/scripts/packages/meshscope"
echo "Bundled skills/mesh-compare/scripts/packages/meshshot"
