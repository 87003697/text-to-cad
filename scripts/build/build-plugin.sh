#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

MODE="write"
CLEAN=0

SOURCE_SKILLS_ROOT="$REPO_ROOT/skills"
PLUGIN_ROOT="$REPO_ROOT/plugins/cad"
TARGET_SKILLS_ROOT="$PLUGIN_ROOT/skills"
CHECK_DIR="${PLUGIN_BUILD_CHECK_DIR:-$REPO_ROOT/tmp/plugin-cad-check}"

SUPPORTED_SKILLS=(
  bambu-labs
  cad
  cad-viewer
  gcode
  sdf
  sendcutsend
  srdf
  step-parts
  urdf
)

usage() {
  cat <<'EOF'
Usage:
  scripts/build/build-plugin.sh [--check] [--clean]

Builds the installable cad plugin package by materializing the root skills/
sources into plugins/cad/skills. The plugin package must not contain symlinks
because provider installers cache plugin roots independently of this checkout.

Options:
  --check  Build into tmp/ and fail if plugins/cad/skills is stale.
  --clean  Remove temporary build/check directories first.
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

ensure_deps() {
  if ! command -v rsync >/dev/null 2>&1; then
    echo "rsync is required to build plugin skill copies." >&2
    exit 1
  fi
  if [ ! -d "$SOURCE_SKILLS_ROOT" ]; then
    echo "Missing source skills directory: $SOURCE_SKILLS_ROOT" >&2
    exit 1
  fi
  if [ ! -d "$PLUGIN_ROOT" ]; then
    echo "Missing plugin directory: $PLUGIN_ROOT" >&2
    exit 1
  fi
}

assert_no_symlinks() {
  local target_dir="$1"
  local first_link
  first_link="$(find "$target_dir" -type l -print -quit)"
  if [ -n "$first_link" ]; then
    echo "Plugin skill copy contains a symlink: $first_link" >&2
    echo "Run scripts/build/build-plugin.sh to materialize plugin skills." >&2
    exit 1
  fi
}

sync_skills() {
  local target_root="$1"
  local skill

  rm -rf "$target_root"
  mkdir -p "$target_root"

  for skill in "${SUPPORTED_SKILLS[@]}"; do
    local source_dir="$SOURCE_SKILLS_ROOT/$skill"
    local target_dir="$target_root/$skill"
    if [ ! -d "$source_dir" ]; then
      echo "Missing source skill directory: skills/$skill" >&2
      exit 1
    fi
    if [ ! -f "$source_dir/SKILL.md" ]; then
      echo "Missing source skill manifest: skills/$skill/SKILL.md" >&2
      exit 1
    fi
    mkdir -p "$target_dir"
    rsync -aL --delete \
      --delete-excluded \
      --exclude __pycache__ \
      --exclude .pytest_cache \
      --exclude '*.pyc' \
      "$source_dir/" "$target_dir/"
  done

  assert_no_symlinks "$target_root"
}

check_skill_names() {
  local target_root="$1"
  local expected actual
  expected="$(printf '%s\n' "${SUPPORTED_SKILLS[@]}" | sort)"
  actual="$(find "$target_root" -maxdepth 1 -mindepth 1 -type d -exec basename {} \; | sort)"
  if [ "$actual" != "$expected" ]; then
    echo "Plugin skill list is stale." >&2
    echo "Expected:" >&2
    printf '%s\n' "$expected" >&2
    echo "Actual:" >&2
    printf '%s\n' "$actual" >&2
    exit 1
  fi
}

check_skills() {
  local check_skills_root="$CHECK_DIR/skills"

  if [ ! -d "$TARGET_SKILLS_ROOT" ]; then
    echo "Missing generated plugin skill copy: plugins/cad/skills" >&2
    echo "Run scripts/build/build-plugin.sh and commit plugins/cad/skills." >&2
    exit 1
  fi

  assert_no_symlinks "$TARGET_SKILLS_ROOT"
  check_skill_names "$TARGET_SKILLS_ROOT"

  if ! diff -qr \
    -x __pycache__ \
    -x .pytest_cache \
    -x '*.pyc' \
    "$check_skills_root" "$TARGET_SKILLS_ROOT" >/tmp/plugin-cad-skills-diff.txt; then
    cat /tmp/plugin-cad-skills-diff.txt >&2
    echo "" >&2
    echo "Plugin skill copy is stale." >&2
    echo "Run scripts/build/build-plugin.sh and commit plugins/cad/skills." >&2
    exit 1
  fi

  echo "Plugin skill copy is up to date."
}

ensure_deps

if [ "$CLEAN" -eq 1 ]; then
  rm -rf "$CHECK_DIR"
fi

if [ "$MODE" = "check" ]; then
  sync_skills "$CHECK_DIR/skills"
  check_skills
else
  sync_skills "$TARGET_SKILLS_ROOT"
  echo "Built plugins/cad/skills"
fi
