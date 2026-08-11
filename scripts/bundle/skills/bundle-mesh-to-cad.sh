#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
MODE="write"
PRINT_OUTPUTS=0

SOURCE="$REPO_ROOT/skills/mesh-to-cad/scripts/mesh-to-cad-review/__main__.py"
TARGET="$REPO_ROOT/.claude/skills/pilot-review/scripts/review.py"
AUTHORITY_SOURCE="$REPO_ROOT/skills/mesh-to-cad/scripts/mesh-to-cad-authority/__main__.py"
AUTHORITY_TARGET="$REPO_ROOT/.claude/skills/pilot-review/scripts/workspace_authority.py"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --check)
      MODE="check"
      ;;
    --clean)
      ;;
    --print-outputs)
      PRINT_OUTPUTS=1
      ;;
    -h|--help)
      echo "Usage: scripts/bundle/bundle-skill.sh mesh-to-cad [--check] [--clean] [--print-outputs]"
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
  shift
done

if [ "$PRINT_OUTPUTS" -eq 1 ]; then
  printf '%s\n' "${TARGET#"$REPO_ROOT"/}" "${AUTHORITY_TARGET#"$REPO_ROOT"/}"
  exit 0
fi

if [ ! -f "$SOURCE" ]; then
  echo "Missing mesh-to-cad reviewer source: ${SOURCE#"$REPO_ROOT/"}" >&2
  exit 1
fi
if [ ! -f "$AUTHORITY_SOURCE" ]; then
  echo "Missing Workspace authority source: ${AUTHORITY_SOURCE#"$REPO_ROOT/"}" >&2
  exit 1
fi

if [ "$MODE" = "check" ]; then
  if [ ! -f "$TARGET" ] || ! cmp -s "$SOURCE" "$TARGET" || \
     [ ! -f "$AUTHORITY_TARGET" ] || ! cmp -s "$AUTHORITY_SOURCE" "$AUTHORITY_TARGET"; then
    echo "pilot-review generated runtime is stale." >&2
    echo "Run scripts/bundle/bundle-skill.sh mesh-to-cad." >&2
    exit 1
  fi
  echo "pilot-review generated runtime is up to date."
  exit 0
fi

mkdir -p "$(dirname "$TARGET")"
cp "$SOURCE" "$TARGET"
cp "$AUTHORITY_SOURCE" "$AUTHORITY_TARGET"
echo "Bundled .claude/skills/pilot-review/scripts/review.py"
echo "Bundled .claude/skills/pilot-review/scripts/workspace_authority.py"
