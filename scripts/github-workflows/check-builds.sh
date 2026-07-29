#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

RUN_BUNDLE_CHECK=1

usage() {
  cat <<'EOF'
Usage:
  scripts/github-workflows/check-builds.sh [--skip-bundle-check]

Checks the production bundle layout. By default this also verifies generated
outputs are fresh with scripts/bundle/bundle.sh --check. Use
--skip-bundle-check only after the current workflow has already run
scripts/bundle/bundle.sh --clean in the same checkout.

Options:
  --skip-bundle-check  Skip the generated-output freshness rebuild.
  -h, --help           Show this help.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --skip-bundle-check)
      RUN_BUNDLE_CHECK=0
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

# Generated output paths production layout must materialize. A stale entry here
# only weakens a redundant guard, because bundling already fails when it cannot
# write an output.
required_paths() {
  cat <<'EOF'
viewer/packages
skills/cad/scripts/packages
skills/cad/scripts/snapshot/runtime
skills/cad-viewer/scripts/viewer
skills/dxf/scripts/packages
skills/implicit-cad/scripts/packages
skills/sdf/scripts/packages
skills/srdf/scripts/packages
skills/urdf/scripts/packages
plugins/cad/skills
EOF
}

# Roots scanned for leftover development symlinks. This list must not drift
# behind the bundle scripts, so derive it from them: every skill with bundle
# logic ships generated copies, and a new bundle-<skill>.sh is covered without
# editing anything here.
symlink_scan_roots() {
  find scripts/bundle/skills -maxdepth 1 -type f -name 'bundle-*.sh' -print |
    sed -e 's|.*/bundle-|skills/|' -e 's|\.sh$||' |
    LC_ALL=C sort
  echo "viewer/packages"
  echo "plugins/cad/skills"
}

check_exists() {
  local path="$1"

  if [ ! -e "$path" ]; then
    echo "Missing production bundle path: $path" >&2
    echo "Run scripts/bundle/bundle.sh --clean and commit the generated outputs." >&2
    exit 1
  fi
}

check_no_symlinks() {
  local root="$1"
  local first_link

  check_exists "$root"

  # Bundling installs dependencies under some roots; only committed paths matter.
  first_link="$(find "$root" -name node_modules -prune -o -type l -print -quit)"
  if [ -n "$first_link" ]; then
    echo "Production bundle paths must not contain symlinks." >&2
    echo "First symlink: $first_link" >&2
    echo "Run scripts/bundle/bundle.sh --clean and commit the generated outputs." >&2
    exit 1
  fi
}

while IFS= read -r required_path; do
  check_exists "$required_path"
done < <(required_paths)

while IFS= read -r scan_root; do
  check_no_symlinks "$scan_root"
done < <(symlink_scan_roots)

if [ "$RUN_BUNDLE_CHECK" -eq 1 ]; then
  "$REPO_ROOT/scripts/bundle/bundle.sh" --check
else
  echo "Skipping bundle freshness rebuild; current workflow already bundled outputs."
fi

echo "Production bundle layout is valid."
