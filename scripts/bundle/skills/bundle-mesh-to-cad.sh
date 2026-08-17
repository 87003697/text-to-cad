#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
MODE="write"

SOURCE="$REPO_ROOT/skills/mesh-to-cad/scripts/mesh-to-cad-review/__main__.py"
TARGET="$REPO_ROOT/.claude/skills/pilot-review/scripts/review.py"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --check)
      MODE="check"
      ;;
    --clean)
      ;;
    -h|--help)
      echo "Usage: scripts/bundle/bundle-skill.sh mesh-to-cad [--check] [--clean]"
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
  shift
done

if [ ! -f "$SOURCE" ]; then
  echo "Missing mesh-to-cad reviewer source: ${SOURCE#"$REPO_ROOT/"}" >&2
  exit 1
fi

if [ "$MODE" = "check" ]; then
  if [ ! -f "$TARGET" ] || ! cmp -s "$SOURCE" "$TARGET"; then
    echo "pilot-review generated runtime is stale." >&2
    echo "Run scripts/bundle/bundle-skill.sh mesh-to-cad." >&2
    exit 1
  fi
  echo "pilot-review generated runtime is up to date."
  exit 0
fi

mkdir -p "$(dirname "$TARGET")"
cp "$SOURCE" "$TARGET"
echo "Bundled .claude/skills/pilot-review/scripts/review.py"
