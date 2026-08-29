#!/usr/bin/env bash
set -euo pipefail

# Installed-plugin smoke: prepare the symlink-free production publish tree,
# install it with the real Codex plugin CLI in an isolated CODEX_HOME, and
# assert the installed cache matches. This is the durable command developers
# should run before making production or paid-pilot claims.
#
# The preparation shares one script with the Release workflow:
#   scripts/release/finalize-publish-tree.sh
# so the smoke exercises exactly what publishes. See CONTEXT.md for the raw
# `codex plugin add` regression this guards against (Codex silently dropping
# tracked skill symlinks on install).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SMOKE_TMP_ROOT="/private/tmp"

SOURCE_ROOT="$REPO_ROOT"
PREPARED_TREE=""
RECEIPT=""
CODEX_BIN="${CODEX_BIN:-codex}"
PYTHON_BIN=""
KEEP_PREPARED=0

usage() {
  cat <<'EOF'
Usage:
  scripts/release/smoke-installed-plugin.sh [--receipt PATH] [--prepared-tree DIR]
                                            [--codex BIN] [--python BIN]
                                            [--keep-prepared-tree]

Runs the full installed-plugin smoke against the current checkout:
  1. Creates a detached git worktree at HEAD.
  2. Runs scripts/bundle/bundle.sh in the worktree using the checkout's
     existing dependency caches (no package download).
  3. Runs scripts/github-workflows/check-builds.sh --skip-bundle-check.
  4. Runs scripts/release/finalize-publish-tree.sh (shared with release.yml).
  5. Installs the resulting tree with the real Codex plugin CLI into a
     task-private CODEX_HOME under /private/tmp, discovers the installed
     cache root from the CLI's JSON output, and asserts symlink-free
     manifest parity plus every formerly-omitted runtime.
  6. Invokes the installed CAD canonical-build entrypoint with the source
     checkout scrubbed from PATH and PYTHON* variables so a silent
     fallback fails closed.
  7. Writes an auditable JSON receipt.

Options:
  --receipt PATH          Where to write the JSON receipt (default:
                          /private/tmp/installed-plugin-smoke-receipt-<pid>.json).
  --prepared-tree DIR     Skip preparation and audit DIR directly. The
                          caller is responsible for having run bundle,
                          check-builds, and finalize-publish-tree in it.
  --codex BIN             Codex CLI executable (default: codex on PATH,
                          or $CODEX_BIN).
  --python BIN            Python interpreter used to import the installed
                          cadgen module (default: ./.venv/bin/python if
                          present, otherwise the ambient python3).
  --keep-prepared-tree    Leave the temporary worktree behind for
                          post-hoc inspection.
  -h, --help              Show this help.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --receipt)
      shift
      RECEIPT="$1"
      ;;
    --prepared-tree)
      shift
      PREPARED_TREE="$1"
      ;;
    --codex)
      shift
      CODEX_BIN="$1"
      ;;
    --python)
      shift
      PYTHON_BIN="$1"
      ;;
    --keep-prepared-tree)
      KEEP_PREPARED=1
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

if [ -z "$PYTHON_BIN" ]; then
  if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
    PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
  else
    PYTHON_BIN="$(command -v python3 || true)"
  fi
fi

if [ -z "$PYTHON_BIN" ] || [ ! -x "$PYTHON_BIN" ]; then
  echo "No usable python interpreter for --python (searched .venv/bin/python and python3)." >&2
  exit 2
fi

if [ -z "$RECEIPT" ]; then
  RECEIPT="$SMOKE_TMP_ROOT/installed-plugin-smoke-receipt-$$.json"
fi
mkdir -p "$(dirname "$RECEIPT")"

if ! command -v "$CODEX_BIN" >/dev/null 2>&1; then
  echo "codex CLI not found: $CODEX_BIN" >&2
  exit 2
fi

# All temp state stays under /private/tmp so nothing persists in the source
# checkout or the developer's real Codex state.
WORKTREE=""
PUBLISH_TREE=""
PUBLISH_ARCHIVE=""
CODEX_HOME_DIR=""
is_owned_temp_path() {
  local path="$1"
  local prefix="$2"
  case "$path" in
    "$SMOKE_TMP_ROOT/$prefix"-*) ;;
    *) return 1 ;;
  esac
  [ "$(dirname "$path")" = "$SMOKE_TMP_ROOT" ] || return 1
  [ ! -L "$path" ] || return 1
  [ "$path" != "$REPO_ROOT" ] || return 1
  [ "$path" != "$SOURCE_ROOT" ] || return 1
}

require_owned_temp_path() {
  local path="$1"
  local prefix="$2"
  if ! is_owned_temp_path "$path" "$prefix"; then
    echo "Refusing unsafe smoke temp path: $path" >&2
    exit 2
  fi
}

remove_owned_temp_dir() {
  local path="$1"
  local prefix="$2"
  [ -n "$path" ] || return 0
  if [ ! -d "$path" ]; then
    return 0
  fi
  if ! is_owned_temp_path "$path" "$prefix"; then
    echo "Refusing cleanup of unsafe smoke temp path: $path" >&2
    return 1
  fi
  rm -rf "$path"
}

remove_owned_temp_file() {
  local path="$1"
  local prefix="$2"
  [ -n "$path" ] || return 0
  if [ ! -f "$path" ]; then
    return 0
  fi
  if ! is_owned_temp_path "$path" "$prefix"; then
    echo "Refusing cleanup of unsafe smoke temp path: $path" >&2
    return 1
  fi
  rm -f "$path"
}

cleanup() {
  local exit_code=$?
  if [ -n "$CODEX_HOME_DIR" ]; then
    remove_owned_temp_dir "$CODEX_HOME_DIR" installed-plugin-smoke-codex || true
  fi
  if [ "$KEEP_PREPARED" -eq 0 ] && [ -n "$WORKTREE" ] && [ -d "$WORKTREE" ]; then
    if ! is_owned_temp_path "$WORKTREE" installed-plugin-smoke-prep; then
      echo "Refusing cleanup of unsafe smoke temp path: $WORKTREE" >&2
    elif [ ! -d "$REPO_ROOT" ] || [ ! -e "$REPO_ROOT/.git" ]; then
      echo "Preserving temporary worktree because its Git owner is unavailable: $WORKTREE" >&2
    elif ! git -C "$REPO_ROOT" worktree remove --force "$WORKTREE" >/dev/null 2>&1; then
      echo "Preserving temporary worktree because Git could not unregister it: $WORKTREE" >&2
    fi
  fi
  if [ "$KEEP_PREPARED" -eq 0 ] && [ -n "$PUBLISH_TREE" ]; then
    remove_owned_temp_dir "$PUBLISH_TREE" installed-plugin-smoke-publish || true
  fi
  remove_owned_temp_file "$PUBLISH_ARCHIVE" installed-plugin-smoke-publish || true
  exit "$exit_code"
}
trap cleanup EXIT INT TERM

require_cached_build_dependencies() {
  local path
  for path in \
    "$REPO_ROOT/viewer/node_modules/.bin/vite" \
    "$REPO_ROOT/packages/meshshot/node_modules/.bin/esbuild" \
    "$REPO_ROOT/tmp/cad-snapshot-build/node_modules/.bin/esbuild" \
    "$REPO_ROOT/tmp/node-builder-build/node_modules/.bin/esbuild"; do
    if [ ! -x "$path" ]; then
      echo "Installed-plugin smoke requires the checkout's existing build dependencies." >&2
      echo "Missing: ${path#"$REPO_ROOT"/}" >&2
      echo "Run the normal dependency setup once, then retry; the smoke does not download packages." >&2
      exit 2
    fi
  done
}

if [ -z "$PREPARED_TREE" ]; then
  WORKTREE="$(mktemp -d /private/tmp/installed-plugin-smoke-prep-XXXXXX)"
  require_owned_temp_path "$WORKTREE" installed-plugin-smoke-prep
  # mktemp created the directory; git worktree needs it to not exist.
  rm -rf "$WORKTREE"
  git -C "$REPO_ROOT" worktree add --detach "$WORKTREE" HEAD >/dev/null
  echo "Prepared worktree: $WORKTREE"

  # A detached worktree intentionally has no ignored dependency trees. Reuse
  # the already-validated local caches strictly as build inputs; all linked
  # roots are either removed before publication or live outside the prepared
  # tree. This keeps the smoke offline and avoids mutating those caches.
  require_cached_build_dependencies
  ln -s "$REPO_ROOT/viewer/node_modules" "$WORKTREE/viewer/node_modules"
  ln -s "$REPO_ROOT/packages/meshshot/node_modules" "$WORKTREE/packages/meshshot/node_modules"

  echo "Bundling production outputs from cached dependencies..."
  "$REPO_ROOT/scripts/bundle/materialize-production-layout.sh" --tree "$WORKTREE"
  (
    cd "$WORKTREE"
    CAD_SNAPSHOT_BUILD_DEPS_DIR="$REPO_ROOT/tmp/cad-snapshot-build" \
    DXF_SNAPSHOT_BUILD_DEPS_DIR="$REPO_ROOT/tmp/cad-snapshot-build" \
    SDF_SNAPSHOT_BUILD_DEPS_DIR="$REPO_ROOT/tmp/cad-snapshot-build" \
    SRDF_SNAPSHOT_BUILD_DEPS_DIR="$REPO_ROOT/tmp/cad-snapshot-build" \
    URDF_SNAPSHOT_BUILD_DEPS_DIR="$REPO_ROOT/tmp/cad-snapshot-build" \
    NODE_BUILDER_BUILD_DEPS_DIR="$REPO_ROOT/tmp/node-builder-build" \
    BUNDLE_INSTALL_DEPS=0 \
      scripts/bundle/bundle.sh
  )

  echo "Validating production bundle layout..."
  (cd "$WORKTREE" && scripts/github-workflows/check-builds.sh --skip-bundle-check)

  echo "Finalizing publish tree (trim + pin + no-symlink check)..."
  "$REPO_ROOT/scripts/release/finalize-publish-tree.sh" --tree "$WORKTREE"

  # Release publishes a Git tree, never the checkout's .git metadata. Stage
  # the finalized detached worktree with normal LFS filters and archive that
  # exact tree object into the directory handed to `codex plugin add`.
  git -C "$WORKTREE" add -A
  publish_tree_oid="$(git -C "$WORKTREE" write-tree)"
  PUBLISH_TREE="$(mktemp -d /private/tmp/installed-plugin-smoke-publish-XXXXXX)"
  require_owned_temp_path "$PUBLISH_TREE" installed-plugin-smoke-publish
  PUBLISH_ARCHIVE="$(mktemp /private/tmp/installed-plugin-smoke-publish-archive-XXXXXX)"
  require_owned_temp_path "$PUBLISH_ARCHIVE" installed-plugin-smoke-publish
  git -C "$WORKTREE" archive --format=tar --output="$PUBLISH_ARCHIVE" "$publish_tree_oid"
  tar -xf "$PUBLISH_ARCHIVE" -C "$PUBLISH_TREE"
  PREPARED_TREE="$PUBLISH_TREE"
  echo "Prepared publish tree: $PREPARED_TREE ($publish_tree_oid)"
fi

if [ ! -d "$PREPARED_TREE" ]; then
  echo "Prepared tree does not exist: $PREPARED_TREE" >&2
  exit 2
fi

CODEX_HOME_DIR="$(mktemp -d /private/tmp/installed-plugin-smoke-codex-XXXXXX)"
require_owned_temp_path "$CODEX_HOME_DIR" installed-plugin-smoke-codex
echo "Isolated CODEX_HOME: $CODEX_HOME_DIR"

echo "Installing plugin through real Codex CLI..."
"$PYTHON_BIN" "$SCRIPT_DIR/smoke_installed_plugin.py" \
  --source-root "$REPO_ROOT" \
  --prepared-tree "$PREPARED_TREE" \
  --receipt "$RECEIPT" \
  --codex "$CODEX_BIN" \
  --python "$PYTHON_BIN" \
  --codex-home "$CODEX_HOME_DIR"

echo "Installed-plugin smoke passed."
echo "Receipt: $RECEIPT"
