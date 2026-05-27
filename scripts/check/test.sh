#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

if [ -z "${PYTHON_BIN:-}" ]; then
  if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
    PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
  else
    PYTHON_BIN="python3"
  fi
fi

section() {
  printf '\n==> %s\n' "$1"
}

run_python_unittest() {
  local name="$1"
  local start_dir="$2"
  local test_files=()

  section "$name"

  while IFS= read -r test_file; do
    test_files+=("$test_file")
  done < <(find "$start_dir" -name 'test*.py' -print | sort)

  if [ "${#test_files[@]}" -eq 0 ]; then
    echo "No Python tests found under $start_dir"
    return 0
  fi

  PYTHONPATH="$REPO_ROOT/$start_dir${PYTHONPATH:+:$PYTHONPATH}" \
    "$PYTHON_BIN" -m unittest "${test_files[@]}"
}

section "cadjs tests"
npm --prefix packages/cadjs test

section "CAD Viewer tests"
npm --prefix viewer run test

section "CAD Viewer production build"
npm --prefix viewer run build

section "CAD Viewer Python package check"
scripts/build/build-viewer.sh --check

section "Documentation checks"
npm --prefix docs run check

section "Plugin package checks"
scripts/check/validate-plugins.sh

section "Generated skill runtime checks"
scripts/build/build-skills.sh --check

run_python_unittest "CAD skill Python tests" "skills/cad/scripts"
run_python_unittest "URDF skill Python tests" "skills/urdf/scripts"
run_python_unittest "SRDF skill Python tests" "skills/srdf/scripts"
run_python_unittest "SDF skill Python tests" "skills/sdf/scripts"
run_python_unittest "G-code skill Python tests" "skills/gcode/scripts"
run_python_unittest "Bambu Labs skill Python tests" "skills/bambu-labs/scripts"
run_python_unittest "MoveIt2 server Python tests" "viewer/moveit2_server"
