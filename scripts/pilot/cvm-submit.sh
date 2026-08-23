#!/usr/bin/env bash
# Submit one detached CVM pilot and print one compact JSON handle.
set -euo pipefail

usage() {
    echo "Usage: $0 pilot <object> <group> [--token-slot N] [--model sol|terra|luna|gpt-5.5] | provider-free installed-plugin <group>" >&2
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
        [[ $# -ge 3 ]] || usage
        object_name="$2"
        group="$3"
        safe_component "$object_name" && safe_group "$group" || usage
        token_slot=""
        model=""
        shift 3
        while [[ $# -gt 0 ]]; do
            [[ $# -ge 2 ]] || usage
            case "$1" in
                --token-slot)
                    [[ -z "$token_slot" && "$2" =~ ^([0-9]|[1-4][0-9])$ ]] || usage
                    token_slot="$2"
                    ;;
                --model)
                    [[ -z "$model" && "$2" =~ ^(sol|terra|luna|gpt-5[.]5)$ ]] || usage
                    model="$2"
                    ;;
                *) usage ;;
            esac
            shift 2
        done
        submit_command="python3 -m scripts.pilot.cvm_job submit-pilot '$object_name' '$group'"
        model_export=""
        if [[ -n "$model" ]]; then
            model_export=" MODEL='$model'"
        fi
        if [[ -n "$token_slot" ]]; then
            remote_command="source \"\$HOME/.secrets/text-to-cad.env\" && [[ '$token_slot' -lt \"\${#VENUS_TOKENS[@]}\" ]] && export VENUS_TOKEN=\"\${VENUS_TOKENS[$token_slot]}\" VENUS_TOKEN_SLOT='$token_slot'$model_export && $submit_command"
        elif [[ -n "$model" ]]; then
            remote_command="export MODEL='$model' && $submit_command"
        else
            remote_command="$submit_command"
        fi
        ;;
    provider-free)
        [[ $# -eq 3 && "$2" == "installed-plugin" ]] || usage
        scenario="$2"
        group="$3"
        safe_group "$group" || usage
        remote_command="python3 -m scripts.pilot.cvm_job submit-provider-free '$scenario' '$group'"
        ;;
    *) usage ;;
esac

exec ssh -n cvm "cd ~/text-to-cad && $remote_command"
