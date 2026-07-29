#!/usr/bin/env bash
# Method α + W: CVM aws s3 cp exp → S3, verify, then rm CVM local. Mac sees via rclone mount.
# See .claude/skills/cvm-pull/SKILL.md.
#
# 假设：
#   - pilot exp dir immutable（一次生成、不再变、杀了重跑产生新 timestamp）
#   - Mac 有 rclone mount ~/threed-code/ 指向 arcwm-code-us-west-2 bucket
#   - CVM AWS user = ericzyma，S3 前缀 = ericzyma/text-to-cad/outputs/
#
# Usage:
#   scripts/utils/cvm-pull.sh                       # 上传 CVM 有但 S3 没有的
#   scripts/utils/cvm-pull.sh --include-byproducts  # 同上，且不 exclude stderr.log/.codex/
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

S3_PREFIX="s3://arcwm-code-us-west-2/ericzyma/text-to-cad/outputs"
MOUNT_PATH="$HOME/threed-code/ericzyma/text-to-cad/outputs"

INCLUDE_BYPRODUCTS=0
[[ "${1:-}" == "--include-byproducts" ]] && INCLUDE_BYPRODUCTS=1

# --- 预检 1: rclone mount 健康
pgrep -f 'rclone mount threed-code' >/dev/null \
  || { echo "rclone mount threed-code: NOT running. Aborting." >&2; exit 4; }

# --- 预检 2: 列 CVM 上的 exp（<group>/<exp> 两层深度）
# _snapshot 目录属于 Mac 端产物，S3 侧已存在；CVM 不会有；无需特殊排除
CVM_EXPS="$(ssh cvm 'find ~/text-to-cad/outputs/ -mindepth 2 -maxdepth 2 -type d -printf "%P\n" 2>/dev/null' | sort)"
[[ -z "$CVM_EXPS" ]] && { echo "No exp on CVM. Nothing to do."; exit 0; }

# --- 预检 3: 列 S3 上已有的 exp（从 rclone mount 视角，两层深度）
mkdir -p "$MOUNT_PATH"
rclone rc --rc-addr=127.0.0.1:5572 vfs/refresh \
    dir="ericzyma/text-to-cad" recursive=false 2>/dev/null || true
LOCAL_EXPS="$(find "$MOUNT_PATH" -mindepth 2 -maxdepth 2 -type d 2>/dev/null | sed "s|$MOUNT_PATH/||" | sort)"

MISSING="$(comm -23 <(echo "$CVM_EXPS") <(echo "$LOCAL_EXPS"))"

if [[ -z "$MISSING" ]]; then
    echo "Already up-to-date. All $(echo "$CVM_EXPS" | wc -l | tr -d ' ') CVM exp(s) present in S3."
    exit 0
fi

N=$(echo "$MISSING" | wc -l | tr -d ' ')
echo "Missing in S3, will upload + clean $N exp(s):"
echo "$MISSING" | sed 's/^/  /'

# --- 组装 aws s3 cp exclude 参数（从 .cvmignore.pull 读）
# 同一份 patterns 需要同时给 aws s3 cp（--exclude）和 verify 步骤的 local find
# 用：一份 EXCLUDE_PATS 变量记录原始 glob，两侧同源避免漂移。
EXCLUDES=()
EXCLUDE_PATS=()
if [[ "$INCLUDE_BYPRODUCTS" -eq 0 ]] && [[ -f .cvmignore.pull ]]; then
    while IFS= read -r pat; do
        [[ -z "$pat" || "$pat" =~ ^# ]] && continue
        EXCLUDES+=(--exclude "$pat")
        EXCLUDE_PATS+=("$pat")
    done < .cvmignore.pull
fi

# 把 aws-glob → grep -E 正则片段（支持 3 种形状：*/X/*, *X, X）
# 用于把 EXCLUDES 应用到 CVM local find 上，让 verify 两侧同 filter
glob_to_regex() {
    local pat="$1"
    pat="${pat//./\\.}"          # 转义 .
    case "$pat" in
        '*/'*'/*')                # */X/* → /X/
            local inner="${pat#\*/}"; inner="${inner%/\*}"
            echo "/$inner/" ;;
        '*'*)                     # *X → X$
            echo "${pat#\*}\$" ;;
        *'*')                     # X* → ^X
            echo "^${pat%\*}" ;;
        *)                        # X → ^X$
            echo "^$pat\$" ;;
    esac
}
LOCAL_FILTER_RGX=""
for pat in "${EXCLUDE_PATS[@]}"; do
    frag="$(glob_to_regex "$pat")"
    [[ -n "$LOCAL_FILTER_RGX" ]] && LOCAL_FILTER_RGX="$LOCAL_FILTER_RGX|"
    LOCAL_FILTER_RGX="$LOCAL_FILTER_RGX$frag"
done

LOG="/tmp/cvm-pull-$(date +%Y%m%d-%H%M%S).log"
echo "Log: $LOG"

# --- Loop：每 exp: upload → verify → clean
i=0
for exp in $MISSING; do
    i=$((i+1))
    echo "=== [$i/$N] $exp ===" | tee -a "$LOG"

    # 1. Upload (CVM 上跑 aws s3 cp)
    # Wrap each pattern in single quotes so CVM's bash treats them as
    # literal strings, not glob patterns. If unquoted, CVM would expand
    # e.g. */.git/* against its cwd (/root/) — the text-to-cad checkout's
    # own .git makes it match real dirs, aws sees them as positional args
    # and bails with "Unknown options: text-to-cad/.git/..." (2026-07-29).
    EXCLUDE_CMD=""
    for pat in "${EXCLUDE_PATS[@]}"; do
        EXCLUDE_CMD+=" --exclude '$pat'"
    done
    # Accept aws exit=2 (warnings only). aws walks the tree first, then
    # applies excludes; if a socket/FIFO tmp file lives inside our
    # excluded dirs (e.g. .codex-upper/tmp/arg0/), the walker warns
    # "Skipping file ... character special device" BEFORE the exclude
    # filter kicks in. Those files are effectively skipped either way
    # so exit=2 (warnings-only) is safe to continue on; only exit >2
    # or exit=1 is a real transfer failure.
    { ssh cvm "aws s3 cp --recursive \
        ~/text-to-cad/outputs/$exp/ $S3_PREFIX/$exp/${EXCLUDE_CMD}" 2>&1 | tee -a "$LOG"; } || true
    aws_rc=${PIPESTATUS[0]}
    if [[ "$aws_rc" -ne 0 && "$aws_rc" -ne 2 ]]; then
        echo "aws s3 cp fatal (exit=$aws_rc) — aborting" | tee -a "$LOG" >&2
        exit "$aws_rc"
    fi

    # 2. Verify file count (CVM local vs S3)。两侧都应用 EXCLUDES 才能对齐。
    if [[ -n "$LOCAL_FILTER_RGX" ]]; then
        LOCAL_N=$(ssh cvm "find ~/text-to-cad/outputs/$exp/ -type f | grep -Ev '$LOCAL_FILTER_RGX' | wc -l")
    else
        LOCAL_N=$(ssh cvm "find ~/text-to-cad/outputs/$exp/ -type f | wc -l")
    fi
    S3_N=$(ssh cvm "aws s3 ls --recursive $S3_PREFIX/$exp/ | wc -l")

    if [[ "$LOCAL_N" -eq "$S3_N" ]]; then
        echo "  verify OK ($LOCAL_N files); cleaning CVM local..." | tee -a "$LOG"
        ssh cvm "rm -rf ~/text-to-cad/outputs/$exp"
    else
        echo "  VERIFY FAILED (local=$LOCAL_N s3=$S3_N); keeping CVM local. Investigate." | tee -a "$LOG"
        exit 5
    fi
done

# --- 让 Mac 立即看到新上传的
# 按处理过的 exp 提取每个 group 名，逐个 refresh 该 group 目录（non-recursive）
# 只 refresh top-level "ericzyma" 不够 — 不会传染到子目录列表。
GROUPS_SEEN="$(echo "$MISSING" | awk -F/ '{print $1}' | sort -u)"
for group in $GROUPS_SEEN; do
    rclone rc --rc-addr=127.0.0.1:5572 vfs/refresh \
        dir="ericzyma/text-to-cad/outputs/$group" recursive=false 2>/dev/null || true
done

echo "Done. Verify: ls $MOUNT_PATH"
