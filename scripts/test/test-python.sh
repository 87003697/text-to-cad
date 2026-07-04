#!/usr/bin/env bash
set -euo pipefail

# shellcheck source=scripts/test/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

LIST_SKILLS_SCRIPT="$REPO_ROOT/scripts/utils/list-skills.sh"

cd "$REPO_ROOT"

run_python_unittest "cadgen package Python tests" "tests/python/packages/cadgen" "packages/cadgen/src"

while IFS= read -r skill; do
  test_dir="tests/python/skills/$skill"
  if [ -d "$test_dir" ]; then
    skill_paths=("skills/$skill/scripts")
    if [ "$skill" = "cad" ] || [ "$skill" = "dxf" ]; then
      skill_paths+=("skills/$skill/scripts/packages/cadgen/src")
    fi
    run_python_unittest "$skill skill Python tests" "$test_dir" "${skill_paths[@]}"
  fi
done < <("$LIST_SKILLS_SCRIPT")

run_python_unittest "MoveIt2 server Python tests" "tests/python/viewer/moveit2_server" "viewer/moveit2_server"
