#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=conda-common.sh
source "${SCRIPT_DIR}/conda-common.sh"

REPO_ROOT="$(find_repo_root)"
CONDA="$(find_conda)"
export RMW_IMPLEMENTATION="${RMW_IMPLEMENTATION:-rmw_cyclonedds_cpp}"

exec "${CONDA}" run --no-capture-output -n "${ROBOT_MOTION_CONDA_ENV_NAME}" \
  motion_server \
  --repo-root "${REPO_ROOT}" \
  --host "${ROBOT_MOTION_HOST:-127.0.0.1}" \
  --port "${ROBOT_MOTION_PORT:-8765}" \
  "$@"
