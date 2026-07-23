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

# --- 预检 2: 列 CVM 上的 exp
CVM_EXPS="$(ssh cvm 'ls ~/text-to-cad/outputs/ 2>/dev/null' | sort)"
[[ -z "$CVM_EXPS" ]] && { echo "No exp on CVM. Nothing to do."; exit 0; }

# --- 预检 3: 列 S3 上已有的 exp（从 rclone mount 视角）
mkdir -p "$MOUNT_PATH"
rclone rc --rc-addr=127.0.0.1:5572 vfs/refresh \
    dir="ericzyma/text-to-cad" recursive=false 2>/dev/null || true
LOCAL_EXPS="$(ls "$MOUNT_PATH" 2>/dev/null | sort)"

MISSING="$(comm -23 <(echo "$CVM_EXPS") <(echo "$LOCAL_EXPS"))"

if [[ -z "$MISSING" ]]; then
    echo "Already up-to-date. All $(echo "$CVM_EXPS" | wc -l | tr -d ' ') CVM exp(s) present in S3."
    exit 0
fi

N=$(echo "$MISSING" | wc -l | tr -d ' ')
echo "Missing in S3, will upload + clean $N exp(s):"
echo "$MISSING" | sed 's/^/  /'

# --- 组装 aws s3 cp exclude 参数（从 .cvmignore.pull 读）
EXCLUDES=()
if [[ "$INCLUDE_BYPRODUCTS" -eq 0 ]] && [[ -f .cvmignore.pull ]]; then
    while IFS= read -r pat; do
        [[ -z "$pat" || "$pat" =~ ^# ]] && continue
        EXCLUDES+=(--exclude "$pat")
    done < .cvmignore.pull
fi

LOG="/tmp/cvm-pull-$(date +%Y%m%d-%H%M%S).log"
echo "Log: $LOG"

# --- Loop：每 exp: upload → verify → clean
i=0
for exp in $MISSING; do
    i=$((i+1))
    echo "=== [$i/$N] $exp ===" | tee -a "$LOG"

    # 1. Upload (CVM 上跑 aws s3 cp)
    ssh cvm "aws s3 cp --recursive \
        ~/text-to-cad/outputs/$exp/ $S3_PREFIX/$exp/ \
        ${EXCLUDES[*]}" 2>&1 | tee -a "$LOG"

    # 2. Verify file count (CVM local vs S3)
    LOCAL_N=$(ssh cvm "find ~/text-to-cad/outputs/$exp/ -type f | wc -l")
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
rclone rc --rc-addr=127.0.0.1:5572 vfs/refresh \
    dir="ericzyma" recursive=false 2>/dev/null || true

echo "Done. Verify: ls $MOUNT_PATH"
