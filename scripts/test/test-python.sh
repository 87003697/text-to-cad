#!/usr/bin/env bash
set -euo pipefail

# shellcheck source=scripts/test/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

LIST_SKILLS_SCRIPT="$REPO_ROOT/scripts/utils/list-skills.sh"

cd "$REPO_ROOT"

run_python_unittest "cadpy package Python tests" "packages/cadpy/tests"
run_python_unittest "cadpy_metadata package Python tests" "packages/cadpy_metadata/tests"

while IFS= read -r skill; do
  if [ -d "skills/$skill/scripts" ]; then
    run_python_unittest "$skill skill Python tests" "skills/$skill/scripts"
  fi
done < <("$LIST_SKILLS_SCRIPT")

run_python_unittest "MoveIt2 server Python tests" "viewer/moveit2_server"
