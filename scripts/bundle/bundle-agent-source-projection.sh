#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PROJECTION_TARGET="$REPO_ROOT/.claude/agent-source-projection"
PROJECTION_MODULE="$REPO_ROOT/scripts/pilot/agent_source_projection.py"
PYTHON_BIN="${PYTHON_BIN:-python3}"

MODE="write"

usage() {
  cat <<'EOF'
Usage:
  scripts/bundle/bundle-agent-source-projection.sh [--check] [--print-outputs]

Materializes the Agent Source Projection into .claude/agent-source-projection/
from five fixed mappings in scripts/pilot/agent_source_projection.py. The
projection is a physical-file tree with a canonical manifest.json; symlinks,
extra files, missing files, or digest drift fail closed.

Options:
  --check          Verify the checked-in projection matches skill source.
  --print-outputs  Print projection output paths relative to the repo root.
  -h, --help       Show this help.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --check)
      MODE="check"
      ;;
    --print-outputs)
      printf '%s\n' \
        '.claude/agent-source-projection/manifest.json' \
        '.claude/agent-source-projection/skills/mesh-to-cad/SKILL.md' \
        '.claude/agent-source-projection/skills/mesh-to-cad/references/candidate-authoring.md' \
        '.claude/agent-source-projection/skills/mesh-to-cad/references/assessment.md' \
        '.claude/agent-source-projection/skills/mesh-to-cad/references/agent-selection-claim.md' \
        '.claude/agent-source-projection/agent-surface/client.py'
      exit 0
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

case "$MODE" in
  check)
    "$PYTHON_BIN" "$PROJECTION_MODULE" check \
      --repo-root "$REPO_ROOT" --target "$PROJECTION_TARGET"
    ;;
  write)
    rm -rf "$PROJECTION_TARGET"
    "$PYTHON_BIN" "$PROJECTION_MODULE" bundle \
      --repo-root "$REPO_ROOT" --target "$PROJECTION_TARGET"
    echo "Bundled .claude/agent-source-projection/"
    ;;
esac
