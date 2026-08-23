#!/usr/bin/env bash
# Read or wait for one detached CVM job through a single keepalive SSH call.
set -euo pipefail

usage() {
    echo "Usage: $0 --once <handle> | --diagnose <handle> | --wait [--until terminal|terminal-or-stale] [--timeout SEC] <handle>" >&2
    exit 2
}

mode="${1:-}"
shift || true
until="terminal"
timeout="43200"

case "$mode" in
    --once)
        [[ $# -eq 1 ]] || usage
        handle="$1"
        remote_command="python3 -m scripts.pilot.cvm_job status '$handle'"
        ;;
    --diagnose)
        [[ $# -eq 1 ]] || usage
        handle="$1"
        remote_command="python3 -m scripts.pilot.cvm_job diagnose '$handle'"
        ;;
    --wait)
        while [[ $# -gt 1 ]]; do
            case "$1" in
                --until)
                    [[ $# -ge 3 ]] || usage
                    until="$2"
                    shift 2
                    ;;
                --timeout)
                    [[ $# -ge 3 ]] || usage
                    timeout="$2"
                    shift 2
                    ;;
                *) usage ;;
            esac
        done
        [[ $# -eq 1 ]] || usage
        handle="$1"
        [[ "$until" == "terminal" || "$until" == "terminal-or-stale" ]] || usage
        [[ "$timeout" =~ ^[0-9]+([.][0-9]+)?$ ]] || usage
        remote_command="python3 -m scripts.pilot.cvm_job wait --until '$until' --timeout '$timeout' '$handle'"
        ;;
    *) usage ;;
esac

if [[ ! "$handle" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$ \
    || "$handle" == batch/* ]]; then
    usage
fi

exec ssh -n \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=6 \
    cvm "cd ~/text-to-cad && $remote_command"
