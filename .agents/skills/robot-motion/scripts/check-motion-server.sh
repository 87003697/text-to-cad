#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=conda-common.sh
source "${SCRIPT_DIR}/conda-common.sh"

REPO_ROOT="$(find_repo_root)"
CONDA="$(find_conda)"
export RMW_IMPLEMENTATION="${RMW_IMPLEMENTATION:-rmw_cyclonedds_cpp}"

"${CONDA}" run --no-capture-output -n "${ROBOT_MOTION_CONDA_ENV_NAME}" motion_server --check --repo-root "${REPO_ROOT}"
