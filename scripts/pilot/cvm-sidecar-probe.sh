#!/usr/bin/env bash
# Stable Browser Sidecar image provisioning and one-shot CVM probe entrypoint.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
case "${1:-}" in
    prepare|provision|probe) ;;
    *)
        echo "Usage: $0 prepare|provision|probe ..." >&2
        exit 2
        ;;
esac
exec python3 "$SCRIPT_DIR/cvm_sidecar_probe.py" "$@"
