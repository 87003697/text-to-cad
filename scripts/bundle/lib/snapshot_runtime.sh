#!/usr/bin/env bash
# Builds the self-contained headless render runtime (render.html + snapshot-render.js)
# that a skill's snapshot CLI drives with Playwright.
#
# Shared because two skills now render: the CAD skill renders STEP models and meshes, and
# the DXF skill renders a drawing's baked flat pattern. Both drive the SAME browser bundle,
# built from the same cadjs entrypoint the CAD Viewer uses, so the picture a snapshot
# produces matches the viewport. A skill may not import another skill's files, so each gets
# its own generated copy rather than reaching across.
#
# Callers set SNAPSHOT_RUNTIME_BUILD_DEPS_DIR (npm scratch) before sourcing, or accept the
# default. Everything else is passed per call.

SNAPSHOT_RUNTIME_ESBUILD_VERSION="${CAD_SNAPSHOT_ESBUILD_VERSION:-0.27.7}"
SNAPSHOT_RUNTIME_THREE_VERSION="${CAD_SNAPSHOT_THREE_VERSION:-0.160.0}"
SNAPSHOT_RUNTIME_GIFENC_VERSION="${CAD_SNAPSHOT_GIFENC_VERSION:-1.0.3}"

snapshot_runtime_entrypoint() {
  printf '%s\n' "$BUNDLE_REPO_ROOT/packages/cadjs/src/common/headlessRenderEntry.js"
}

snapshot_runtime_need_install() {
  local deps_dir="$1"
  [ -x "$deps_dir/node_modules/.bin/esbuild" ] || return 0
  node <<EOF || return 0
const deps = {
  esbuild: "$SNAPSHOT_RUNTIME_ESBUILD_VERSION",
  three: "$SNAPSHOT_RUNTIME_THREE_VERSION",
  gifenc: "$SNAPSHOT_RUNTIME_GIFENC_VERSION",
};
for (const [name, expected] of Object.entries(deps)) {
  const actual = require("$deps_dir/node_modules/" + name + "/package.json").version;
  if (actual !== expected) {
    process.exit(1);
  }
}
EOF
  return 1
}

# ensure_snapshot_runtime_deps <deps_dir> <install_allowed>
ensure_snapshot_runtime_deps() {
  local deps_dir="$1"
  local install_allowed="${2:-1}"
  for tool in npm node; do
    if ! command -v "$tool" >/dev/null 2>&1; then
      echo "$tool is required to build the snapshot runtime." >&2
      exit 1
    fi
  done
  if snapshot_runtime_need_install "$deps_dir"; then
    if [ "$install_allowed" -eq 0 ]; then
      echo "Missing or stale build dependencies in $deps_dir." >&2
      echo "Run without --no-install to install them." >&2
      exit 1
    fi
    mkdir -p "$deps_dir"
    npm install --prefix "$deps_dir" --no-audit --no-fund \
      --fetch-retries=1 --fetch-timeout=10000 \
      "esbuild@$SNAPSHOT_RUNTIME_ESBUILD_VERSION" \
      "three@$SNAPSHOT_RUNTIME_THREE_VERSION" \
      "gifenc@$SNAPSHOT_RUNTIME_GIFENC_VERSION"
  fi
}

write_snapshot_render_html() {
  local target_dir="$1"
  cat > "$target_dir/render.html" <<'EOF'
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>CAD snapshot render</title>
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

# build_snapshot_runtime <target_dir> <deps_dir>
build_snapshot_runtime() {
  local target_dir="$1"
  local deps_dir="$2"
  rm -rf "$target_dir"
  mkdir -p "$target_dir"
  write_snapshot_render_html "$target_dir"
  # NODE_PATH resolves cadjs's bare `implicitjs` imports (e.g.
  # cadjs/common/camera.js re-exports implicitjs/common/camera.js) directly
  # from packages/ source, honoring implicitjs's exports map, so the bundle
  # stays hermetic on fresh checkouts with no packages/cadjs/node_modules.
  # A directory --alias cannot do this: it bypasses the exports map.
  NODE_PATH="$BUNDLE_REPO_ROOT/packages" \
    "$deps_dir/node_modules/.bin/esbuild" "$(snapshot_runtime_entrypoint)" \
    --bundle \
    --format=esm \
    --platform=browser \
    --target=es2022 \
    --main-fields=module,main \
    --minify \
    --legal-comments=none \
    --alias:three="$deps_dir/node_modules/three" \
    --alias:gifenc="$deps_dir/node_modules/gifenc/dist/gifenc.esm.js" \
    --outfile="$target_dir/snapshot-render.js"
}

# check_snapshot_runtime <runtime_dir> <check_dir> <repo_relative_label> <fix_hint>
check_snapshot_runtime() {
  local runtime_dir="$1"
  local check_dir="$2"
  local label="$3"
  local fix_hint="$4"
  local stale=0
  for file in render.html snapshot-render.js; do
    if [ ! -f "$runtime_dir/$file" ]; then
      echo "Missing generated runtime file: $label/$file" >&2
      stale=1
      continue
    fi
    if ! cmp -s "$check_dir/$file" "$runtime_dir/$file"; then
      echo "Stale generated runtime file: $label/$file" >&2
      stale=1
    fi
  done
  if [ "$stale" -ne 0 ]; then
    echo "" >&2
    echo "$fix_hint" >&2
    return 1
  fi
  return 0
}
