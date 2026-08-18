#!/usr/bin/env bash
# Submit one detached CVM pilot and print one compact JSON handle.
set -euo pipefail

usage() {
    echo "Usage: $0 pilot <object> <group> [--token-slot N]" >&2
    exit 2
}

safe_component() {
    [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ \
        && "$1" != "." && "$1" != ".." ]]
}

safe_group() {
    [[ "$1" =~ ^[0-9]{8}-[0-9]{6}-[a-z0-9-]+$ ]]
}

mode="${1:-}"
case "$mode" in
    pilot)
        [[ $# -eq 3 || $# -eq 5 ]] || usage
        object_name="$2"
        group="$3"
        safe_component "$object_name" && safe_group "$group" || usage
        submit_command="python3 -m scripts.pilot.cvm_job submit-pilot '$object_name' '$group'"
        if [[ $# -eq 5 ]]; then
            [[ "$4" == "--token-slot" && "$5" =~ ^([0-9]|[1-4][0-9])$ ]] || usage
            token_slot="$5"
            remote_command="source \"\$HOME/.secrets/text-to-cad.env\" && [[ '$token_slot' -lt \"\${#VENUS_TOKENS[@]}\" ]] && export VENUS_TOKEN=\"\${VENUS_TOKENS[$token_slot]}\" VENUS_TOKEN_SLOT='$token_slot' && $submit_command"
        else
            remote_command="$submit_command"
        fi
        ;;
    *) usage ;;
esac

exec ssh -n cvm "cd ~/text-to-cad && $remote_command"
