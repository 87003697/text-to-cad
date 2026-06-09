#!/usr/bin/env bash
set -u

# Hydrate CAD fixtures from the local Git LFS object cache only.
# This intentionally does not download or clean model files.
payload="$(cat)"
cwd="${CLAUDE_PROJECT_DIR:-$PWD}"

if command -v jq >/dev/null 2>&1; then
  parsed_cwd="$(printf '%s' "$payload" | jq -r '.cwd // empty' 2>/dev/null || true)"
  if [ -n "$parsed_cwd" ]; then
    cwd="$parsed_cwd"
  fi
fi

repo_root="$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "$repo_root" ] || [ ! -d "$repo_root/models" ]; then
  exit 0
fi

if [ -x "$repo_root/scripts/dev/setup-symlinks.sh" ]; then
  "$repo_root/scripts/dev/setup-symlinks.sh" --check >/dev/null 2>&1 \
    || "$repo_root/scripts/dev/setup-symlinks.sh" >&2 \
    || true
fi

if git -C "$repo_root" lfs version >/dev/null 2>&1; then
  git -C "$repo_root" lfs checkout models >&2 || true
fi

if command -v rg >/dev/null 2>&1 \
  && rg -q "version https://git-lfs.github.com/spec/v1" "$repo_root/models"; then
  echo "Some models are still Git LFS pointers; local LFS cache is missing objects." >&2
  echo 'To download them explicitly, run: git lfs pull --include="models/**" --exclude=""' >&2
fi
