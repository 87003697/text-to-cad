#!/usr/bin/env bash

ROBOT_MOTION_CONDA_ENV_NAME="${ROBOT_MOTION_CONDA_ENV_NAME:-motion-server-ros2}"

find_conda() {
  if [[ -n "${ROBOT_MOTION_CONDA_EXE:-}" && -x "${ROBOT_MOTION_CONDA_EXE}" ]]; then
    printf '%s\n' "${ROBOT_MOTION_CONDA_EXE}"
    return 0
  fi
  if [[ -n "${CONDA_EXE:-}" && -x "${CONDA_EXE}" ]]; then
    printf '%s\n' "${CONDA_EXE}"
    return 0
  fi
  if command -v conda >/dev/null 2>&1; then
    command -v conda
    return 0
  fi
  local candidate
  for candidate in \
    "${HOME}/miniforge3/bin/conda" \
    "${HOME}/mambaforge/bin/conda" \
    "${HOME}/miniconda3/bin/conda" \
    "${HOME}/anaconda3/bin/conda" \
    "/opt/conda/bin/conda"
  do
    if [[ -x "${candidate}" ]]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done
  printf 'Could not find conda. Install Miniforge, put conda on PATH, or set ROBOT_MOTION_CONDA_EXE/CONDA_EXE.\n' >&2
  return 1
}

find_repo_root() {
  if [[ -n "${ROBOT_MOTION_REPO_ROOT:-}" ]]; then
    if [[ ! -d "${ROBOT_MOTION_REPO_ROOT}" ]]; then
      printf 'ROBOT_MOTION_REPO_ROOT does not exist: %s\n' "${ROBOT_MOTION_REPO_ROOT}" >&2
      return 1
    fi
    (cd "${ROBOT_MOTION_REPO_ROOT}" && pwd -P)
    return 0
  fi

  local root
  if root="$(git -C "${PWD}" rev-parse --show-toplevel 2>/dev/null)"; then
    printf '%s\n' "${root}"
    return 0
  fi

  printf 'Could not find a repository root from the current directory. Run from the robot project repo or set ROBOT_MOTION_REPO_ROOT.\n' >&2
  return 1
}
