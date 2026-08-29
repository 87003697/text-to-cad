#!/usr/bin/env bash
# Submit one detached CVM pilot and print one compact JSON handle.
set -euo pipefail

usage() {
    echo "Usage: $0 pilot <object> <group> [--token-slot N] [--model sol|terra|luna|gpt-5.5] (default: gpt-5.5) [--plugin-mode direct|e2e] [--view-image|--no-view-image] (default: --view-image) [--reconstruction-spec|--no-reconstruction-spec] (default: --reconstruction-spec) | provider-free installed-plugin|agent-surface-mcp-injection|agent-surface-mcp-ephemeral-differential <group>" >&2
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
        plugin_mode=""
        view_image=1
        view_image_flag_seen=0
        reconstruction_spec=1
        reconstruction_spec_flag_seen=0
        shift 3
        while [[ $# -gt 0 ]]; do
            case "$1" in
                --reconstruction-spec)
                    [[ "$reconstruction_spec_flag_seen" == 0 ]] || usage
                    reconstruction_spec=1
                    reconstruction_spec_flag_seen=1
                    shift
                    ;;
                --no-reconstruction-spec)
                    [[ "$reconstruction_spec_flag_seen" == 0 ]] || usage
                    reconstruction_spec=0
                    reconstruction_spec_flag_seen=1
                    shift
                    ;;
                --view-image)
                    [[ "$view_image_flag_seen" == 0 ]] || usage
                    view_image=1
                    view_image_flag_seen=1
                    shift
                    ;;
                --no-view-image)
                    [[ "$view_image_flag_seen" == 0 ]] || usage
                    view_image=0
                    view_image_flag_seen=1
                    shift
                    ;;
                --token-slot)
                    [[ $# -ge 2 ]] || usage
                    [[ -z "$token_slot" && "$2" =~ ^([0-9]|[1-4][0-9])$ ]] || usage
                    token_slot="$2"
                    shift 2
                    ;;
                --model)
                    [[ $# -ge 2 ]] || usage
                    [[ -z "$model" && "$2" =~ ^(sol|terra|luna|gpt-5[.]5)$ ]] || usage
                    model="$2"
                    shift 2
                    ;;
                --plugin-mode)
                    [[ $# -ge 2 ]] || usage
                    [[ -z "$plugin_mode" && "$2" =~ ^(direct|e2e)$ ]] || usage
                    plugin_mode="$2"
                    shift 2
                    ;;
                *) usage ;;
            esac
        done
        selected_model="${model:-${MODEL:-}}"
        if [[ -n "$selected_model" && ! "$selected_model" =~ ^(sol|terra|luna|gpt-5[.]5)$ ]]; then
            usage
        fi
        submit_command="python3 -m scripts.pilot.cvm_job submit-pilot '$object_name' '$group' --plugin-mode '${plugin_mode:-direct}'"
        if [[ -n "$selected_model" ]]; then
            submit_command+=" --model '$selected_model'"
        fi
        if [[ "$view_image" == 1 ]]; then
            submit_command+=" --view-image"
        else
            submit_command+=" --no-view-image"
        fi
        if [[ "$reconstruction_spec" == 1 ]]; then
            submit_command+=" --reconstruction-spec"
        else
            submit_command+=" --no-reconstruction-spec"
        fi
        model_export=""
        if [[ -n "$selected_model" ]]; then
            model_export=" MODEL='$selected_model'"
        fi
        if [[ -n "$token_slot" ]]; then
            remote_command="source \"\$HOME/.secrets/text-to-cad.env\" && [[ '$token_slot' -lt \"\${#VENUS_TOKENS[@]}\" ]] && export MODEL && export VENUS_TOKEN=\"\${VENUS_TOKENS[$token_slot]}\" VENUS_TOKEN_SLOT='$token_slot'$model_export && $submit_command"
        elif [[ -n "$selected_model" ]]; then
            remote_command="export MODEL='$selected_model' && $submit_command"
        else
            remote_command="$submit_command"
        fi
        ;;
    provider-free)
        [[ $# -eq 3 && ( "$2" == "installed-plugin" || "$2" == "agent-surface-mcp-injection" || "$2" == "agent-surface-mcp-ephemeral-differential" ) ]] || usage
        scenario="$2"
        group="$3"
        safe_group "$group" || usage
        remote_command="python3 -m scripts.pilot.cvm_job submit-provider-free '$scenario' '$group'"
        ;;
    *) usage ;;
esac

exec ssh -n cvm "cd ~/text-to-cad && $remote_command"
