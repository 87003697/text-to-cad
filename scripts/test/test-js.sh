#!/usr/bin/env bash
set -euo pipefail

# shellcheck source=scripts/test/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

cd "$REPO_ROOT"

section "cadjs tests"
npm --prefix packages/cadjs test

section "meshshot tests"
npm --prefix packages/meshshot test

section "browser runtime fixed render service tests"
node --test packages/browser_runtime/image/cad-render-service.test.cjs

section "CAD Viewer tests"
npm --prefix viewer run test
