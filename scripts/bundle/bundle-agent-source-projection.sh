#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PROJECTION_TARGET="$REPO_ROOT/.claude/agent-source-projection"
PROJECTION_MODULE="$REPO_ROOT/scripts/pilot/agent_source_projection.py"
PYTHON_BIN="${PYTHON_BIN:-python3}"

MODE="write"
PRINT_OUTPUTS=0

usage() {
  cat <<'EOF'
Usage:
  scripts/bundle/bundle-agent-source-projection.sh [--check] [--clean] [--print-outputs]

Materializes the Agent Source Projection into .claude/agent-source-projection/
from the canonical allowlist in scripts/pilot/agent_source_projection.py. The
projection is a physical-file tree with a canonical manifest.json; symlinks,
extra files, missing files, or digest drift fail closed.

Options:
  --check          Verify the checked-in projection matches skill source.
  --clean          Remove the projection tree before materializing.
  --print-outputs  Print projection output paths relative to the repo root.
  -h, --help       Show this help.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --check)
      MODE="check"
      ;;
    --clean)
      MODE="clean"
      ;;
    --print-outputs)
      PRINT_OUTPUTS=1
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

if [ "$PRINT_OUTPUTS" -eq 1 ]; then
  "$PYTHON_BIN" "$PROJECTION_MODULE" print-outputs --repo-root "$REPO_ROOT"
  exit 0
fi

case "$MODE" in
  check)
    "$PYTHON_BIN" "$PROJECTION_MODULE" check \
      --repo-root "$REPO_ROOT" --target "$PROJECTION_TARGET"
    ;;
  clean)
    rm -rf "$PROJECTION_TARGET"
    "$PYTHON_BIN" "$PROJECTION_MODULE" materialize \
      --repo-root "$REPO_ROOT" --target "$PROJECTION_TARGET"
    echo "Bundled .claude/agent-source-projection/"
    ;;
  write)
    "$PYTHON_BIN" "$PROJECTION_MODULE" materialize \
      --repo-root "$REPO_ROOT" --target "$PROJECTION_TARGET"
    echo "Bundled .claude/agent-source-projection/"
    ;;
esac
