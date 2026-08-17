#!/usr/bin/env bash
set -euo pipefail

secrets_file="${HOME}/.secrets/text-to-cad.env"
if [[ "${1:-}" == "submit" && -z "${VENUS_TOKEN:-}" && -f "$secrets_file" ]]; then
    # shellcheck disable=SC1090
    source "$secrets_file"
fi

exec python3 -m scripts.pilot.cvm_agent "$@"
