#!/usr/bin/env bash
set -euo pipefail

COPIED_VENV_MARKER=".text-to-cad-copied-venv"

usage() {
  cat <<'EOF'
Usage:
  scripts/dev/agent-env-setup.sh [--source-checkout PATH] [--skip-venv-copy] [--skip-venv-sync]

Prepares an agent worktree for local CAD development:
  - restores the development symlink layout
  - hydrates models from the local Git LFS cache only
  - copies a source checkout .venv as a bootstrap cache when this worktree lacks one
  - reconciles the copied/current .venv with requirements-dev.txt

Environment:
  TEXT_TO_CAD_SOURCE_CHECKOUT  Source checkout to copy .venv from.
  TEXT_TO_CAD_SKIP_VENV_COPY   Set to 1 to skip .venv copying.
  TEXT_TO_CAD_SKIP_VENV_SYNC   Set to 1 to skip pip reconciliation.
  TEXT_TO_CAD_ALLOW_VENV_DOWNLOADS
                                Set to 1 to allow normal pip build isolation.
  TEXT_TO_CAD_REQUIRE_VENV_SYNC Set to 1 to fail setup when reconciliation fails.
EOF
}

log() {
  printf '%s\n' "$*" >&2
}

json_field() {
  local payload="$1"
  local field="$2"
  if [ -z "$payload" ] || ! command -v jq >/dev/null 2>&1; then
    return 0
  fi
  printf '%s' "$payload" | jq -r --arg field "$field" '.[$field] // empty' 2>/dev/null || true
}

abs_path() {
  local path="$1"
  if [ -d "$path" ]; then
    (cd "$path" && pwd -P)
  else
    return 1
  fi
}

safe_rm_copy_tmp() {
  local path="$1"
  case "$path" in
    */.venv.copy.*)
      rm -rf "$path"
      ;;
  esac
}

infer_source_checkout() {
  local repo_root="$1"
  local explicit_source="${TEXT_TO_CAD_SOURCE_CHECKOUT:-}"
  if [ -n "$explicit_source" ] && [ -x "$explicit_source/.venv/bin/python" ]; then
    abs_path "$explicit_source"
    return 0
  fi

  local common_dir
  common_dir="$(git -C "$repo_root" rev-parse --git-common-dir 2>/dev/null || true)"
  if [ -z "$common_dir" ]; then
    return 0
  fi
  case "$common_dir" in
    /*) ;;
    *) common_dir="$repo_root/$common_dir" ;;
  esac

  local candidate
  candidate="$(cd "$common_dir/.." 2>/dev/null && pwd -P || true)"
  if [ -n "$candidate" ] && [ -x "$candidate/.venv/bin/python" ]; then
    abs_path "$candidate"
  fi
}

copy_venv_if_needed() {
  local repo_root="$1"
  local source_checkout="$2"
  local target_venv="$repo_root/.venv"

  if [ "${TEXT_TO_CAD_SKIP_VENV_COPY:-}" = "1" ]; then
    log "Skipping .venv copy because TEXT_TO_CAD_SKIP_VENV_COPY=1."
    return 0
  fi
  if [ -e "$target_venv" ]; then
    log "Using existing .venv in $repo_root."
    return 0
  fi
  if [ -z "$source_checkout" ]; then
    log "No source checkout with .venv found; skipping .venv copy."
    return 0
  fi

  local source_venv="$source_checkout/.venv"
  if [ ! -x "$source_venv/bin/python" ]; then
    log "Source checkout .venv is missing a Python executable; skipping .venv copy: $source_venv"
    return 0
  fi
  if [ "$source_checkout" = "$repo_root" ]; then
    log "Source checkout is the current checkout; skipping .venv copy."
    return 0
  fi

  local tmp_venv="$repo_root/.venv.copy.$$"
  safe_rm_copy_tmp "$tmp_venv"
  trap 'safe_rm_copy_tmp "$tmp_venv"' EXIT

  log "Copying .venv from $source_checkout as a bootstrap cache."
  if ! cp -cR "$source_venv" "$tmp_venv" 2>/dev/null; then
    safe_rm_copy_tmp "$tmp_venv"
    if command -v rsync >/dev/null 2>&1; then
      mkdir -p "$tmp_venv"
      rsync -a "$source_venv/" "$tmp_venv/"
    else
      cp -a "$source_venv" "$tmp_venv"
    fi
  fi

  touch "$tmp_venv/$COPIED_VENV_MARKER"
  printf '%s\n' "$source_checkout" > "$tmp_venv/.text-to-cad-copied-venv-source"
  mv "$tmp_venv" "$target_venv"
  trap - EXIT
  log "Copied .venv to $target_venv."
}

sync_venv_if_available() {
  local repo_root="$1"
  local python_bin="$repo_root/.venv/bin/python"

  if [ "${TEXT_TO_CAD_SKIP_VENV_SYNC:-}" = "1" ]; then
    log "Skipping .venv sync because TEXT_TO_CAD_SKIP_VENV_SYNC=1."
    return 0
  fi
  if [ ! -x "$python_bin" ]; then
    log "No executable .venv/bin/python found; skipping dependency sync."
    return 0
  fi
  if [ ! -f "$repo_root/requirements-dev.txt" ]; then
    log "No requirements-dev.txt found; skipping dependency sync."
    return 0
  fi

  local pip_args=()
  if [ "${TEXT_TO_CAD_ALLOW_VENV_DOWNLOADS:-}" != "1" ]; then
    if ! "$python_bin" - <<'PY'
import importlib.metadata
import importlib.util
import sys

if importlib.util.find_spec("setuptools") is None:
    sys.exit(1)

try:
    version = importlib.metadata.version("setuptools")
except importlib.metadata.PackageNotFoundError:
    sys.exit(1)

parts = []
for raw in version.split("."):
    digits = ""
    for char in raw:
        if not char.isdigit():
            break
        digits += char
    if digits:
        parts.append(int(digits))
    else:
        break

sys.exit(0 if tuple(parts) >= (69,) else 1)
PY
    then
      if [ "${TEXT_TO_CAD_REQUIRE_VENV_SYNC:-}" = "1" ]; then
        log "Required .venv reconciliation cannot run offline; setuptools>=69 is missing."
        return 1
      fi
      log "Skipping .venv reconciliation; setuptools>=69 is missing and downloads are disabled."
      log "Set TEXT_TO_CAD_ALLOW_VENV_DOWNLOADS=1 to allow pip to fetch build dependencies."
      return 0
    fi
    pip_args+=("--no-build-isolation")
  fi

  log "Reconciling .venv with requirements-dev.txt."
  if ! "$python_bin" -m pip install "${pip_args[@]}" -r "$repo_root/requirements-dev.txt"; then
    if [ "${TEXT_TO_CAD_REQUIRE_VENV_SYNC:-}" = "1" ]; then
      return 1
    fi
    log "Warning: .venv reconciliation failed; continuing with the existing environment."
    log "Set TEXT_TO_CAD_ALLOW_VENV_DOWNLOADS=1 to allow pip to fetch build dependencies."
  fi
}

payload=""
if [ ! -t 0 ]; then
  payload="$(cat || true)"
fi

start_dir="${CLAUDE_PROJECT_DIR:-$PWD}"
payload_cwd="$(json_field "$payload" "cwd")"
if [ -n "$payload_cwd" ]; then
  start_dir="$payload_cwd"
fi

while [ "$#" -gt 0 ]; do
  case "$1" in
    --source-checkout)
      if [ "$#" -lt 2 ]; then
        echo "--source-checkout requires a path" >&2
        exit 2
      fi
      TEXT_TO_CAD_SOURCE_CHECKOUT="$2"
      shift 2
      ;;
    --source-checkout=*)
      TEXT_TO_CAD_SOURCE_CHECKOUT="${1#--source-checkout=}"
      shift
      ;;
    --skip-venv-copy)
      TEXT_TO_CAD_SKIP_VENV_COPY=1
      shift
      ;;
    --skip-venv-sync)
      TEXT_TO_CAD_SKIP_VENV_SYNC=1
      shift
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
done

repo_root="$(git -C "$start_dir" rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "$repo_root" ]; then
  log "Not inside a git worktree; skipping text-to-cad agent setup."
  exit 0
fi
repo_root="$(abs_path "$repo_root")"
cd "$repo_root"

if [ -x scripts/dev/setup-symlinks.sh ]; then
  scripts/dev/setup-symlinks.sh --check || scripts/dev/setup-symlinks.sh
fi

if [ -d models ] && git lfs version >/dev/null 2>&1; then
  log "Hydrating models from local Git LFS cache."
  git lfs checkout models || true
fi

if [ -d models ] \
  && command -v rg >/dev/null 2>&1 \
  && rg -q "version https://git-lfs.github.com/spec/v1" models; then
  log "Some models are still LFS pointers; local LFS cache is missing objects."
  log 'To download them explicitly, run: git lfs pull --include="models/**" --exclude=""'
fi

source_checkout="$(infer_source_checkout "$repo_root")"
copy_venv_if_needed "$repo_root" "$source_checkout"
sync_venv_if_available "$repo_root"
