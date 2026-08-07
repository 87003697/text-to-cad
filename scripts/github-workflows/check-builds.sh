#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

RUN_BUNDLE_CHECK=1

usage() {
  cat <<'EOF'
Usage:
  scripts/github-workflows/check-builds.sh [--skip-bundle-check]

Checks the production bundle layout. By default this also verifies generated
outputs are fresh with scripts/bundle/bundle.sh --check. Use
--skip-bundle-check only after the current workflow has already run
scripts/bundle/bundle.sh --clean in the same checkout.

Options:
  --skip-bundle-check  Skip the generated-output freshness rebuild.
  -h, --help           Show this help.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --skip-bundle-check)
      RUN_BUNDLE_CHECK=0
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

# Ask the bundle scripts which paths they generate rather than repeating them
# here, so this check cannot fall behind a new or renamed bundle output.
generated_paths() {
  "$REPO_ROOT/scripts/bundle/bundle-skill.sh" --all --print-outputs
}

check_generated_path() {
  local root="$1"
  local first_link

  if [ ! -e "$root" ]; then
    echo "Missing production bundle path: $root" >&2
    echo "Run scripts/bundle/bundle.sh --clean and commit the generated outputs." >&2
    exit 1
  fi

  # This assertion is load-bearing, not tidiness. The three installers each treat
  # symlinks differently, and one of them loses data silently:
  #   - Skills CLI (npx skills): dereferences them into real files.
  #   - Claude Code plugin install: preserves them verbatim.
  #   - Codex plugin add: SILENTLY DROPS them. copy_dir_recursive in
  #     codex-rs/core-plugins/src/store.rs branches only on is_dir()/is_file(),
  #     and DirEntry::file_type() does not traverse symlinks, so a symlinked
  #     entry matches neither branch and never reaches the plugin cache. No
  #     error, no warning -- the file is simply missing at runtime.
  # A symlink that reaches the published tree therefore ships a broken skill to
  # every Codex user. Do not relax this check.
  #
  # Generated roots are production outputs, including any node_modules copied
  # into a self-contained runtime. Scan all of them; a dependency symlink here
  # is just as capable of disappearing during installation as any other link.
  first_link="$(find "$root" -type l -print -quit)"
  if [ -n "$first_link" ]; then
    echo "Production bundle paths must not contain symlinks." >&2
    echo "First symlink: $first_link" >&2
    echo "Run scripts/bundle/bundle.sh --clean and commit the generated outputs." >&2
    exit 1
  fi
}

check_published_skills_symlinks() {
  local first_link=""
  local tracked_path

  # The repo root is the plugin root, so every committed path below skills/ is
  # part of the publish tree. Inspect symlinks across that whole surface, not
  # only paths known to a bundler. Use the Git index to distinguish publishable
  # content from untracked development dependencies such as a local
  # skills/*/node_modules tree.
  while IFS= read -r -d '' tracked_path; do
    if [ -L "$tracked_path" ]; then
      first_link="$tracked_path"
      break
    fi
  done < <(git ls-files -z -- skills)

  if [ -n "$first_link" ]; then
    echo "Published skills must not contain symlinks." >&2
    echo "First tracked symlink: $first_link" >&2
    echo "Run scripts/bundle/bundle.sh --clean and commit physical production files." >&2
    exit 1
  fi
}

while IFS= read -r generated_path; do
  check_generated_path "$generated_path"
done < <(generated_paths)

check_published_skills_symlinks

if [ "$RUN_BUNDLE_CHECK" -eq 1 ]; then
  "$REPO_ROOT/scripts/bundle/bundle.sh" --check
else
  echo "Skipping bundle freshness rebuild; current workflow already bundled outputs."
fi

echo "Production bundle layout is valid."
