#!/usr/bin/env bash
# Controlled CVM Codex CLI runtime selector.
set -euo pipefail

SCHEMA="text-to-cad.cvm-codex-runtime/1"

usage() {
    echo "Usage: $0 install 0.147.0|0.148.0 | status | probe" >&2
    exit 2
}

action="${1:-}"
case "$action" in
    install)
        [[ $# -eq 2 && ( "$2" == "0.147.0" || "$2" == "0.148.0" ) ]] || usage
        ;;
    status|probe)
        [[ $# -eq 1 ]] || usage
        ;;
    *) usage ;;
esac

exec ssh cvm "bash -s -- '$action' '${2:-}'" <<'REMOTE'
set -euo pipefail

VERSION="${2:-}"
SCHEMA="text-to-cad.cvm-codex-runtime/1"
RUNTIME_ROOT="/usr/local/lib/text-to-cad/codex"
CANDIDATE="$RUNTIME_ROOT/$VERSION"
CANDIDATE_CODEX="$CANDIDATE/node_modules/.bin/codex"
SELECTOR="/usr/local/bin/codex"
EXPECTED_VERSION="codex-cli $VERSION"

allowed_version() {
    [[ "$1" == "0.147.0" || "$1" == "0.148.0" ]]
}

installed_versions() {
    local directory name count=0 separator=""
    printf '['
    for directory in "$RUNTIME_ROOT"/*; do
        [ -d "$directory" ] || continue
        name="${directory##*/}"
        [[ "$name" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || continue
        [ "$count" -lt 8 ] || break
        printf '%s"%s"' "$separator" "$name"
        separator=','
        count=$((count + 1))
    done
    printf ']'
}

safe_version() {
    if [[ "$1" =~ ^codex-cli\ [0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        printf '%s' "$1"
    fi
}

emit() {
    printf '{"schema":"%s","action":"%s","status":"%s","selector":"%s","resolved":"%s","version":"%s","installed_versions":%s,"mcp_list_exit":%s}\n' \
        "$SCHEMA" "$1" "$2" "$SELECTOR" "$3" "$4" "$(installed_versions)" "$5"
}

resolved_selector() {
    [ -x "$SELECTOR" ] || return 1
    readlink -f "$SELECTOR"
}

probe_runtime() {
    local action="$1" resolved version mcp_status
    resolved="$(resolved_selector)" || {
        emit "$action" "missing" "" "" 127
        return 1
    }
    case "$resolved" in
        /usr/*) ;;
        *)
            emit "$action" "unaudited" "$resolved" "" 127
            return 1
            ;;
    esac
    version="$("$SELECTOR" --version 2>&1 || true)"
    if ! safe_version "$version" >/dev/null || ! allowed_version "${version#codex-cli }"; then
        emit "$action" "version_mismatch" "$resolved" "$(safe_version "$version")" 127
        return 1
    fi
    if "$SELECTOR" mcp list >/dev/null 2>&1; then
        mcp_status=0
    else
        mcp_status=$?
    fi
    if [ "$mcp_status" -ne 0 ]; then
        emit "$action" "mcp_failed" "$resolved" "$version" "$mcp_status"
        return 1
    fi
    emit "$action" "succeeded" "$resolved" "$version" 0
}

validate_runtime() {
    local executable="$1" runtime_root="$2" resolved version
    resolved="$(readlink -f "$executable")" || return 1
    case "$resolved" in
        "$runtime_root"/*) ;;
        *) return 1 ;;
    esac
    version="$("$resolved" --version 2>&1 || true)"
    [ "$version" = "$EXPECTED_VERSION" ] || return 1
    "$resolved" mcp list >/dev/null 2>&1 || return 1
    printf '%s' "$resolved"
}

switch_selector() {
    local executable="$1" temporary_selector
    temporary_selector="$SELECTOR.$$.new"
    ln -s "$executable" "$temporary_selector"
    mv -f "$temporary_selector" "$SELECTOR"
}

case "$1" in
    install)
        allowed_version "$VERSION" || exit 2
        mkdir -p "$RUNTIME_ROOT"
        exec 9>"$RUNTIME_ROOT/.install.lock"
        flock 9
        staging="$(mktemp -d "$RUNTIME_ROOT/.staging-$VERSION.XXXXXX")"
        cleanup_install() {
            [ -z "$staging" ] || rm -rf "$staging"
        }
        trap cleanup_install EXIT
        trap 'exit 143' INT TERM
        if ! npm install --prefix "$staging" --no-package-lock --omit=dev "@openai/codex@$VERSION" >/dev/null 2>&1; then
            emit install "install_failed" "" "" 127
            exit 1
        fi
        staging_codex="$staging/node_modules/.bin/codex"
        staging_resolved="$(validate_runtime "$staging_codex" "$staging")" || {
            emit install "candidate_invalid" "$staging_codex" "" 1
            exit 1
        }
        if [ -e "$CANDIDATE" ] || [ -L "$CANDIDATE" ]; then
            if [ ! -d "$CANDIDATE" ] || [ -L "$CANDIDATE" ]; then
                emit install "existing_invalid" "$CANDIDATE_CODEX" "" 1
                exit 1
            fi
            target_resolved="$(validate_runtime "$CANDIDATE_CODEX" "$CANDIDATE")" || {
                emit install "existing_invalid" "$CANDIDATE_CODEX" "" 1
                exit 1
            }
        else
            staging_suffix="${staging_resolved#"$staging"/}"
            mv "$staging" "$CANDIDATE"
            staging=""
            target_resolved="$CANDIDATE/$staging_suffix"
        fi
        if ! switch_selector "$target_resolved"; then
            emit install "selector_failed" "$CANDIDATE_CODEX" "" 1
            exit 1
        fi
        emit install "succeeded" "$CANDIDATE_CODEX" "$EXPECTED_VERSION" 0
        ;;
    status)
        probe_runtime status
        ;;
    probe)
        probe_runtime probe
        ;;
    *) exit 2 ;;
esac
REMOTE
