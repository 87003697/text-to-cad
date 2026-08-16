#!/usr/bin/env bash
set -euo pipefail

usage() {
    echo "Usage: $0 submit surface-adaptation | monitor [--wait] <cvma-handle>" >&2
    exit 2
}

sha256_file() {
    openssl dgst -sha256 "$1" | awk '{print $NF}'
}

mode="${1:-}"
case "$mode" in
    submit)
        [[ $# -eq 2 && "$2" == "surface-adaptation" ]] || usage
        [[ -z "$(git status --porcelain)" ]] || {
            echo "cvm-agent: source worktree must be clean" >&2
            exit 3
        }
        source_revision="$(git rev-parse HEAD)"
        module_sha="$(sha256_file scripts/pilot/cvm_agent.py)"
        prompt_sha="$(sha256_file scripts/pilot/cvm_agent_surface_prompt.md)"
        source_digest="$(python3 -m scripts.pilot.cvm_agent source-digest)"
        exec ssh -n cvm \
            "cd ~/text-to-cad && scripts/pilot/cvm-agent-remote.sh submit surface-adaptation --source-revision '$source_revision' --module-sha256 '$module_sha' --prompt-sha256 '$prompt_sha' --source-digest '$source_digest'"
        ;;
    monitor)
        shift
        wait_flag=""
        if [[ "${1:-}" == "--wait" ]]; then
            wait_flag="--wait"
            shift
        fi
        [[ $# -eq 1 && "$1" =~ ^cvma-[0-9a-f]{24}$ ]] || usage
        exec ssh -n cvm \
            "cd ~/text-to-cad && scripts/pilot/cvm-agent-remote.sh monitor $wait_flag '$1'"
        ;;
    *) usage ;;
esac
