#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  cat <<'EOF'
Usage:
  scripts/build.sh [--check] [--clean] [--no-install] [--no-build]

Universal generated-runtime build wrapper.

Options are forwarded to scripts/build/build-skills.sh.
EOF
  exit 0
fi

cd "$REPO_ROOT"
"$SCRIPT_DIR/build/build-skills.sh" "$@"
