#!/usr/bin/env bash
#
# toys4k-batch.sh <slug> <obj1> [<obj2> ...]
#
# Runs multiple toys4k pilots in parallel, one per Venus token.
# Concurrency = len(VENUS_TOKENS). Extra objects queue on a FIFO
# semaphore and pick up freed tokens automatically — same code path
# whether N ≤ K or N > K.
#
# Requires:
#   VENUS_TOKENS   bash array (from ~/.secrets/text-to-cad.env),
#                  e.g. VENUS_TOKENS=(tok1 tok2 tok3 tok4 tok5)
#
# Layout:
#   outputs/<GROUP>/
#     <ts>-<obj>/       ← each pilot's exp dir (from toys4k-pilot.sh)
#     batch-<obj>.log   ← this batch script's per-pilot stdout/stderr
#
# Token safety:
#   Tokens flow through env only; nothing logs the raw value. Per-pilot
#   log shows only `token#<index>` (position in the pool).
#
# Exit codes:
#   0  all pilots exited 0
#   1  preflight / usage failure
#   N  count of pilots that exited non-zero

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd -P)"
cd "$REPO_ROOT"

# --- args ------------------------------------------------------------
SLUG="${1:?Usage: toys4k-batch.sh <slug> <obj>... (slug = kebab-case tag)}"
shift
[[ $# -ge 1 ]] || { echo "toys4k-batch: need at least one <obj>" >&2; exit 1; }
[[ "$SLUG" =~ ^[a-z0-9-]+$ ]] \
    || { echo "toys4k-batch: bad slug '$SLUG', want [a-z0-9-]+" >&2; exit 1; }

# --- token pool ------------------------------------------------------
# Bash arrays don't survive `export`, so even if the caller sourced
# ~/.secrets/text-to-cad.env in a parent shell, this child process
# won't see VENUS_TOKENS. Source it ourselves if unset. Override the
# path via TEXT_TO_CAD_SECRETS if needed (test rigs, non-default host).
if ! declare -p VENUS_TOKENS >/dev/null 2>&1; then
    SECRETS="${TEXT_TO_CAD_SECRETS:-$HOME/.secrets/text-to-cad.env}"
    [[ -f "$SECRETS" ]] \
        || { echo "toys4k-batch: secrets file not found: $SECRETS" >&2; exit 1; }
    # shellcheck disable=SC1090
    source "$SECRETS"
fi
if ! declare -p VENUS_TOKENS >/dev/null 2>&1; then
    echo "toys4k-batch: VENUS_TOKENS still unset after sourcing secrets" >&2
    exit 1
fi
K="${#VENUS_TOKENS[@]}"
[[ "$K" -ge 1 ]] || { echo "toys4k-batch: VENUS_TOKENS is empty" >&2; exit 1; }

# --- group dir -------------------------------------------------------
GROUP="$(date +%Y%m%d-%H%M%S)-$SLUG"
mkdir -p "outputs/$GROUP"
echo "[batch] group=$GROUP  objs=$#  concurrency=$K"

# --- FIFO-backed counting semaphore ---------------------------------
# Open r/w on fd 9, unlink path (still usable via fd), seed with K tokens.
# Each pilot's subshell blocks on `read -u 9` until a token is available;
# returns the token via `echo >&9` on EXIT so it's never leaked even on
# pilot crash.
FIFO="$(mktemp -u -t batch-tokens.XXXXXX)"
mkfifo "$FIFO"
exec 9<>"$FIFO"
rm "$FIFO"
for tok in "${VENUS_TOKENS[@]}"; do echo "$tok" >&9; done

# Kill all child pilots on Ctrl-C / signal.
trap 'jobs -p | xargs -r kill 2>/dev/null || true' INT TERM

# --- fan out ---------------------------------------------------------
pids=()
for obj in "$@"; do
    (
        # Acquire a token from the pool (blocks if all in use).
        read -u 9 tok
        # Return it to the pool no matter how this subshell exits.
        trap 'echo "$tok" >&9' EXIT

        # Resolve position in pool for readable logging (never log tok itself).
        idx="?"
        for i in "${!VENUS_TOKENS[@]}"; do
            [[ "${VENUS_TOKENS[$i]}" == "$tok" ]] && { idx="$i"; break; }
        done
        echo "[batch] $(date +%H:%M:%S) $obj → token#$idx (pilot start)"

        VENUS_TOKEN="$tok" \
            "$SCRIPT_DIR/toys4k-pilot.sh" "$obj" "$GROUP" \
            > "outputs/$GROUP/batch-$obj.log" 2>&1
        rc=$?
        echo "[batch] $(date +%H:%M:%S) $obj ← token#$idx (pilot exit=$rc)"
        exit $rc
    ) &
    pids+=($!)
done

# --- wait & aggregate ------------------------------------------------
fail=0
for pid in "${pids[@]}"; do
    wait "$pid" || fail=$((fail + 1))
done
exec 9>&-
echo "[batch] done: $# pilots, $fail failed → outputs/$GROUP/"
exit "$fail"
