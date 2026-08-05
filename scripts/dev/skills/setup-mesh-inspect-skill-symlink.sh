#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
UTILS_SCRIPT="$REPO_ROOT/scripts/dev/symlink-utils.sh"

MODE="write"

usage() {
  cat <<'EOF'
Usage:
  scripts/dev/setup-skill-symlink.sh mesh-inspect [--check]

Sets up the mesh-inspect skill development meshscope symlink.

Options:
  --check     Verify the symlink without changing files.
  -h, --help  Show this help.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --check)
      MODE="check"
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

# shellcheck source=scripts/dev/symlink-utils.sh
source "$UTILS_SCRIPT"

cd "$REPO_ROOT"
setup_link \
  "$MODE" \
  "skills/mesh-inspect/scripts/packages/meshscope" \
  "../../../../packages/meshscope"
