#!/usr/bin/env bash
set -euo pipefail

# Vendors packages/cadpy_metadata into skills/urdf/scripts/packages/cadpy_metadata.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export BUNDLE_REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
# shellcheck source=../lib/vendor.sh
source "$SCRIPT_DIR/../lib/vendor.sh"

vendor_python_skill --skill urdf --package cadpy_metadata "$@"
