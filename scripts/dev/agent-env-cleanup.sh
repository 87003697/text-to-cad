#!/usr/bin/env bash
set -euo pipefail

COPIED_VENV_MARKER=".text-to-cad-copied-venv"
APPLY=0
CURRENT_ONLY=0
DEAD_WORKTREES=0
SCAN_ROOTS=()

usage() {
  cat <<'EOF'
Usage:
  scripts/dev/agent-env-cleanup.sh [--current-only] [--dead-worktrees] [--dry-run|--apply] [--scan-root PATH]

Tidies local-only agent caches without touching models/ or .git/lfs/objects.

Modes:
  --current-only      Remove this checkout's .venv only when it has the copied-venv marker.
  --dead-worktrees    Remove copied .venv/cache dirs from stale worktree directories.

Safety:
  The default is --dry-run. Use --apply to delete files.
  .venv is removed only when .venv/.text-to-cad-copied-venv exists.
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

remove_path() {
  local path="$1"
  if [ ! -e "$path" ]; then
    return 0
  fi
  if [ "$APPLY" -eq 1 ]; then
    log "Removing $path"
    rm -rf "$path"
  else
    log "Would remove $path"
  fi
}

cleanup_copied_venv() {
  local worktree="$1"
  local venv="$worktree/.venv"
  if [ -f "$venv/$COPIED_VENV_MARKER" ]; then
    remove_path "$venv"
  else
    log "Leaving .venv in place; copied-venv marker not present: $venv"
  fi
}

cleanup_agent_caches() {
  local worktree="$1"
  cleanup_copied_venv "$worktree"
  remove_path "$worktree/node_modules"
  remove_path "$worktree/.vite"
  remove_path "$worktree/.pytest_cache"
  remove_path "$worktree/.mypy_cache"
  remove_path "$worktree/.ruff_cache"
  remove_path "$worktree/tmp"
}

registered_worktrees() {
  git worktree list --porcelain 2>/dev/null | awk '
    /^worktree / { path = substr($0, 10); print path }
  '
}

prunable_worktrees() {
  git worktree list --porcelain 2>/dev/null | awk '
    /^worktree / {
      if (path != "" && prunable == 1) {
        print path
      }
      path = substr($0, 10)
      prunable = 0
      next
    }
    /^prunable/ {
      prunable = 1
      next
    }
    END {
      if (path != "" && prunable == 1) {
        print path
      }
    }
  '
}

is_registered_worktree() {
  local candidate="$1"
  local registered
  while IFS= read -r registered; do
    if [ "$candidate" = "$registered" ]; then
      return 0
    fi
  done < <(registered_worktrees)
  return 1
}

cleanup_dead_worktrees() {
  local path

  while IFS= read -r path; do
    if [ -d "$path" ]; then
      log "Cleaning prunable worktree caches: $path"
      cleanup_agent_caches "$path"
    fi
  done < <(prunable_worktrees)

  local root marker worktree
  for root in "${SCAN_ROOTS[@]}"; do
    if [ ! -d "$root" ]; then
      continue
    fi
    while IFS= read -r marker; do
      worktree="$(cd "$(dirname "$marker")/.." && pwd -P)"
      if is_registered_worktree "$worktree"; then
        log "Skipping registered worktree: $worktree"
        continue
      fi
      log "Cleaning stale copied-venv worktree caches: $worktree"
      cleanup_agent_caches "$worktree"
    done < <(find "$root" -path "*/.venv/$COPIED_VENV_MARKER" -type f -print 2>/dev/null)
  done
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
payload_worktree_path="$(json_field "$payload" "worktree_path")"
if [ -n "$payload_worktree_path" ]; then
  start_dir="$payload_worktree_path"
fi

while [ "$#" -gt 0 ]; do
  case "$1" in
    --current-only)
      CURRENT_ONLY=1
      shift
      ;;
    --dead-worktrees)
      DEAD_WORKTREES=1
      shift
      ;;
    --dry-run)
      APPLY=0
      shift
      ;;
    --apply)
      APPLY=1
      shift
      ;;
    --scan-root)
      if [ "$#" -lt 2 ]; then
        echo "--scan-root requires a path" >&2
        exit 2
      fi
      SCAN_ROOTS+=("$2")
      shift 2
      ;;
    --scan-root=*)
      SCAN_ROOTS+=("${1#--scan-root=}")
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

if [ "$CURRENT_ONLY" -eq 0 ] && [ "$DEAD_WORKTREES" -eq 0 ]; then
  CURRENT_ONLY=1
fi

repo_root="$(git -C "$start_dir" rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "$repo_root" ] && [ -d "$start_dir" ]; then
  repo_root="$start_dir"
fi
if [ -z "$repo_root" ]; then
  log "Could not resolve a worktree for cleanup."
  exit 0
fi
repo_root="$(abs_path "$repo_root")"
cd "$repo_root"

if [ "${#SCAN_ROOTS[@]}" -eq 0 ]; then
  if [ -n "${HOME:-}" ]; then
    SCAN_ROOTS+=("$HOME/.codex/worktrees")
    SCAN_ROOTS+=("$HOME/.claude/worktrees")
  fi
fi

if [ "$CURRENT_ONLY" -eq 1 ]; then
  cleanup_copied_venv "$repo_root"
fi

if [ "$DEAD_WORKTREES" -eq 1 ]; then
  cleanup_dead_worktrees
fi
