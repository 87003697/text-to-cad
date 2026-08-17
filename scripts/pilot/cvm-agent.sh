#!/usr/bin/env bash
set -euo pipefail

usage() {
    echo "Usage: $0 submit broker-readiness | monitor [--wait] <cvma-handle>" >&2
    exit 2
}

sha256_file() {
    openssl dgst -sha256 "$1" | awk '{print $NF}'
}

mode="${1:-}"
case "$mode" in
    submit)
        [[ $# -eq 2 && "$2" == "broker-readiness" ]] || usage
        [[ -z "$(git status --porcelain)" ]] || {
            echo "cvm-agent: source worktree must be clean" >&2
            exit 3
        }
        source_revision="$(git rev-parse HEAD)"
        module_sha="$(sha256_file scripts/pilot/cvm_agent.py)"
        prompt_sha="$(sha256_file scripts/pilot/cvm_agent_broker_prompt.md)"
        read -r source_digest source_manifest \
            < <(python3 -m scripts.pilot.cvm_agent source-identity)
        exec ssh -n cvm \
            "cd ~/text-to-cad && scripts/pilot/cvm-agent-remote.sh submit broker-readiness --source-revision '$source_revision' --module-sha256 '$module_sha' --prompt-sha256 '$prompt_sha' --source-digest '$source_digest' --source-manifest-base64 '$source_manifest'"
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
