#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

MODE="write"
CAD_ARGS=()
CAD_VIEWER_ARGS=()
URDF_ARGS=()
SRDF_ARGS=()
SDF_ARGS=()

usage() {
  cat <<'EOF'
Usage:
  scripts/build/build-skills.sh [--check] [--clean] [--no-install] [--no-build]

Builds all generated skill runtimes.

Options:
  --check       Build into tmp/ and fail if checked-in skill runtimes are stale.
  --clean       Remove temporary build/check directories first.
  --no-install  Require existing CAD snapshot build dependencies.
  --no-build    Reuse the current viewer/dist for the cad-viewer skill.
  -h, --help    Show this help.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --check)
      MODE="check"
      CAD_ARGS+=("--check")
      CAD_VIEWER_ARGS+=("--check")
      URDF_ARGS+=("--check")
      SRDF_ARGS+=("--check")
      SDF_ARGS+=("--check")
      ;;
    --clean)
      CAD_ARGS+=("--clean")
      CAD_VIEWER_ARGS+=("--clean")
      URDF_ARGS+=("--clean")
      SRDF_ARGS+=("--clean")
      SDF_ARGS+=("--clean")
      ;;
    --no-install)
      CAD_ARGS+=("--no-install")
      ;;
    --no-build)
      CAD_VIEWER_ARGS+=("--no-build")
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

if [ "$MODE" = "check" ]; then
  echo "Checking URDF skill build outputs..."
else
  echo "Building URDF skill..."
fi
if [ "${#URDF_ARGS[@]}" -gt 0 ]; then
  "$SCRIPT_DIR/build-urdf-skill.sh" "${URDF_ARGS[@]}"
else
  "$SCRIPT_DIR/build-urdf-skill.sh"
fi

if [ "$MODE" = "check" ]; then
  echo "Checking SRDF skill build outputs..."
else
  echo "Building SRDF skill..."
fi
if [ "${#SRDF_ARGS[@]}" -gt 0 ]; then
  "$SCRIPT_DIR/build-srdf-skill.sh" "${SRDF_ARGS[@]}"
else
  "$SCRIPT_DIR/build-srdf-skill.sh"
fi

if [ "$MODE" = "check" ]; then
  echo "Checking SDF skill build outputs..."
else
  echo "Building SDF skill..."
fi
if [ "${#SDF_ARGS[@]}" -gt 0 ]; then
  "$SCRIPT_DIR/build-sdf-skill.sh" "${SDF_ARGS[@]}"
else
  "$SCRIPT_DIR/build-sdf-skill.sh"
fi

if [ "$MODE" = "check" ]; then
  echo "Checking CAD skill build outputs..."
else
  echo "Building CAD skill..."
fi
if [ "${#CAD_ARGS[@]}" -gt 0 ]; then
  "$SCRIPT_DIR/build-cad-skill.sh" "${CAD_ARGS[@]}"
else
  "$SCRIPT_DIR/build-cad-skill.sh"
fi

if [ "$MODE" = "check" ]; then
  echo "Checking cad-viewer skill build outputs..."
else
  echo "Building cad-viewer skill..."
fi
if [ "${#CAD_VIEWER_ARGS[@]}" -gt 0 ]; then
  "$SCRIPT_DIR/build-cad-viewer-skill.sh" "${CAD_VIEWER_ARGS[@]}"
else
  "$SCRIPT_DIR/build-cad-viewer-skill.sh"
fi

if [ "$MODE" = "check" ]; then
  echo "All skill build outputs are up to date."
else
  echo "Built all skills."
fi
