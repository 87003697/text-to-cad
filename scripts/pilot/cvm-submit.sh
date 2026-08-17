#!/usr/bin/env bash
# Submit one detached CVM pilot and print one compact JSON handle.
set -euo pipefail

usage() {
    echo "Usage: $0 pilot <object> <group> [cvmsp-runtime-handle]" >&2
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
        [[ $# -eq 3 || $# -eq 4 ]] || usage
        object_name="$2"
        group="$3"
        safe_component "$object_name" && safe_group "$group" || usage
        runtime_handle="${4:-}"
        if [[ -n "$runtime_handle" ]]; then
            [[ "$runtime_handle" =~ ^cvmsp-[0-9a-f]{24}$ ]] || usage
            remote_command="TTC_BROWSER_RUNTIME_PROVISION_HANDLE='$runtime_handle' python3 -m scripts.pilot.cvm_job submit-pilot '$object_name' '$group'"
        else
            remote_command="python3 -m scripts.pilot.cvm_job submit-pilot '$object_name' '$group'"
        fi
        ;;
    *) usage ;;
esac

exec ssh -n cvm "cd ~/text-to-cad && $remote_command"
