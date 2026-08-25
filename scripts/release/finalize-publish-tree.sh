#!/usr/bin/env bash
set -euo pipefail

# Trim development-only roots, pin cadgen requirements, and validate the
# resulting production tree. Run against a checkout that has already been
# bundled (scripts/bundle/bundle.sh --clean) and validated
# (scripts/github-workflows/check-builds.sh). This is the single source of
# truth used by both the Release workflow and the installed-plugin smoke; any
# rule that gates what may ship lives here so the smoke tests exactly what
# release publishes.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

TREE_ROOT="$(pwd)"

usage() {
  cat <<'EOF'
Usage:
  scripts/release/finalize-publish-tree.sh [--tree <dir>] [--print-removed-roots]

Transforms the current working tree (or --tree <dir>) into the production
publish tree by dropping development-only roots, pinning cadgen requirements
to the canonical release version, and asserting the resulting tree contains
no symlinks and no repo-root packages/ references from published skills.

Options:
  --tree <dir>            Operate on <dir> instead of the current directory.
  --print-removed-roots   After finalizing, print the removed-roots list on
                          stdout so callers can propagate it.
  -h, --help              Show this help.
EOF
}

PRINT_REMOVED_ROOTS=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --tree)
      shift
      TREE_ROOT="$1"
      ;;
    --print-removed-roots)
      PRINT_REMOVED_ROOTS=1
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

if [ ! -d "$TREE_ROOT" ]; then
  echo "Tree root does not exist: $TREE_ROOT" >&2
  exit 2
fi

cd "$TREE_ROOT"

# The plugin package is the repository root, so everything that reaches the
# target branch is copied into every install. None of these is part of what
# installs; see .github/workflows/release.yml for the rationale on each root.
REMOVED_ROOTS="models viewer tests requirements-dev.txt docs packages"

for root in $REMOVED_ROOTS; do
  rm -rf "$root"
  if [ -e "$root" ]; then
    echo "Failed to remove $root from the publish tree." >&2
    exit 1
  fi
done

if [ ! -f skills/cad-viewer/scripts/viewer/package.json ]; then
  echo "Refusing to publish without the bundled CAD Viewer runtime:" >&2
  echo "  skills/cad-viewer/scripts/viewer/package.json is missing." >&2
  exit 1
fi

# Every skill vendors what it needs, so no skill SOURCE may reach repo-root
# packages/ -- one that does would break silently on install rather than here.
# Generated artifacts are exempt: a bundled dist/ is self-contained, and its
# sourcemaps name the original source paths as debug metadata, which is not a
# runtime reference.
skill_packages_refs() {
  grep -rIlE '\.\./\.\./\.\./packages/|["'"'"']\.\./\.\./packages/' skills \
    --exclude='*.map' --exclude-dir=dist 2>/dev/null || true
}
if [ -n "$(skill_packages_refs)" ]; then
  echo "A skill references repo-root packages/, which is not published:" >&2
  skill_packages_refs >&2
  exit 1
fi

# Published skills have no sibling packages/cadgen to install editable from,
# so the publish tree resolves cadgen from PyPI at this release's version. Run
# after the trim so the fallback vendored copies (which stay in each skill's
# scripts/packages/cadgen) are pinned too, and re-run --check to fail loud on
# anything the rewrite missed.
#
# pin-cadgen-requirements.sh computes its own REPO_ROOT relative to its
# BASH_SOURCE, then `cd`s into it. Invoke the copy that lives in $TREE_ROOT so
# the rewrite lands there — not in the developer's source checkout when this
# script is run with --tree.
if [ ! -x "$TREE_ROOT/scripts/release/pin-cadgen-requirements.sh" ]; then
  echo "Tree is missing scripts/release/pin-cadgen-requirements.sh — cannot pin cadgen." >&2
  exit 1
fi
"$TREE_ROOT/scripts/release/pin-cadgen-requirements.sh"
"$TREE_ROOT/scripts/release/pin-cadgen-requirements.sh" --check

# The Agent Source Projection is bundled at develop time; assert its presence
# and verify its embedded manifest before publishing. Fail loud here so a
# release cannot ship an isolated Agent Execution without its projection.
if [ ! -f "$TREE_ROOT/scripts/pilot/agent_source_projection.py" ]; then
  echo "Publish tree is missing scripts/pilot/agent_source_projection.py." >&2
  exit 1
fi
python3 "$TREE_ROOT/scripts/pilot/agent_source_projection.py" verify \
  --target "$TREE_ROOT/.claude/agent-source-projection"

# Provider installers each treat symlinks differently and Codex silently drops
# them (see scripts/github-workflows/check-builds.sh for the full explanation).
# A symlink surviving into the publish tree ships a broken skill to every
# Codex user. Fail loud instead.
first_link="$(find . -type l -not -path './.git/*' -print -quit)"
if [ -n "$first_link" ]; then
  echo "Publish tree must not contain symlinks." >&2
  echo "First symlink: $first_link" >&2
  exit 1
fi

echo "Publish tree finalized (removed: $REMOVED_ROOTS)."

if [ "$PRINT_REMOVED_ROOTS" -eq 1 ]; then
  printf '%s\n' "$REMOVED_ROOTS"
fi
