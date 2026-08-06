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
#   scripts/utils/cvm-pull.sh
#   scripts/utils/cvm-pull.sh --include-byproducts
#   scripts/utils/cvm-pull.sh --discard-postmortem
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

S3_PREFIX="s3://arcwm-code-us-west-2/ericzyma/text-to-cad/outputs"
S3_REMOTE="threed-code:arcwm-code-us-west-2/ericzyma/text-to-cad/outputs"
MOUNT_PATH="$HOME/threed-code/ericzyma/text-to-cad/outputs"
RCLONE_RC_ADDR="127.0.0.1:5572"

INCLUDE_BYPRODUCTS=0
DISCARD_POSTMORTEM=0
for arg in "$@"; do
    case "$arg" in
        --include-byproducts) INCLUDE_BYPRODUCTS=1 ;;
        --discard-postmortem) DISCARD_POSTMORTEM=1 ;;
        *)
            echo "Usage: $0 [--include-byproducts] [--discard-postmortem]" >&2
            exit 2
            ;;
    esac
done
if [[ "$INCLUDE_BYPRODUCTS" -eq 1 && "$DISCARD_POSTMORTEM" -eq 1 ]]; then
    echo "--include-byproducts and --discard-postmortem are mutually exclusive" >&2
    exit 2
fi

# --- 预检 1: rclone mount 健康
# 直接探测本 workflow 依赖的 RC endpoint，避免 Codex sandbox 无法读取 macOS
# process table 时把健康 mount 误判为未运行。
rclone rc --rc-addr="$RCLONE_RC_ADDR" core/version >/dev/null 2>&1 \
  || { echo "rclone RC endpoint: NOT reachable. Aborting." >&2; exit 4; }

# --- 预检 2: 列 CVM 上的 exp（<group>/<exp> 两层深度）
# _snapshot 目录属于 Mac 端产物，S3 侧已存在；CVM 不会有；无需特殊排除
CVM_EXPS="$(ssh -n cvm 'find ~/text-to-cad/outputs/ -mindepth 2 -maxdepth 2 -type d -printf "%P\n" 2>/dev/null' | sort)"
[[ -z "$CVM_EXPS" ]] && { echo "No exp on CVM. Nothing to do."; exit 0; }

# cleanup target 会进入 remote shell command；只允许两段安全目录名。
while IFS= read -r exp; do
    [[ -z "$exp" ]] && continue
    group_part="${exp%%/*}"
    exp_part="${exp#*/}"
    if [[ ! "$exp" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ \
        || "$group_part" == "." || "$group_part" == ".." \
        || "$exp_part" == "." || "$exp_part" == ".." ]]; then
        echo "Unsafe CVM exp path: $exp" >&2
        exit 7
    fi
done <<< "$CVM_EXPS"

# --- 预检 3: 直接列 S3 上已有的 exp（不把 VFS cache 当 source of truth）
mkdir -p "$MOUNT_PATH"
if ! S3_DIRS="$(rclone lsf "$S3_REMOTE" \
    --dirs-only --recursive --max-depth 2 2>/dev/null)"; then
    echo "Cannot list S3 output prefixes through rclone remote" >&2
    exit 4
fi
S3_EXPS="$(printf '%s\n' "$S3_DIRS" \
    | awk -F/ 'NF >= 3 && $1 != "" && $2 != "" { print $1 "/" $2 }' \
    | sort -u)"

MISSING="$(comm -23 \
    <(printf '%s\n' "$CVM_EXPS" | sed '/^$/d') \
    <(printf '%s\n' "$S3_EXPS" | sed '/^$/d'))"

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
# Bash 3.2 + `set -u` treats expansion of an empty array as unbound. Keep one
# empty sentinel and skip it in consumers so an empty/missing ignore file works.
EXCLUDE_PATS=("")
if [[ "$INCLUDE_BYPRODUCTS" -eq 0 ]] && [[ -f .cvmignore.pull ]]; then
    while IFS= read -r pat; do
        [[ -z "$pat" || "$pat" =~ ^# ]] && continue
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
    [[ -z "$pat" ]] && continue
    frag="$(glob_to_regex "$pat")"
    [[ -n "$LOCAL_FILTER_RGX" ]] && LOCAL_FILTER_RGX="$LOCAL_FILTER_RGX|"
    LOCAL_FILTER_RGX="$LOCAL_FILTER_RGX$frag"
done

LOG="${TMPDIR:-/tmp}/cvm-pull-$(date +%Y%m%d-%H%M%S).log"
echo "Log: $LOG"

# --- Loop：每 exp: upload → verify → clean
i=0
UPLOADED=""
SKIPPED=""
while IFS= read -r exp; do
    [[ -z "$exp" ]] && continue
    i=$((i+1))
    echo "=== [$i/$N] $exp ===" | tee -a "$LOG"

    # v4 Runner 会为非零状态保留 .codex-upper 供 postmortem。默认 pull 不能
    # exclude 这份状态后又删除整个 CVM exp；只有两个显式 flag 能越过此门。
    # -n is required here and for every SSH below: this loop reads MISSING
    # from stdin, and a normal ssh client would consume the remaining exp rows.
    REMOTE_STATUS="$(ssh -n cvm "python3 -c \
        'import json,pathlib; p=pathlib.Path.home()/\"text-to-cad/outputs/$exp/artifact_manifest.json\"; print(json.loads(p.read_text()).get(\"final_status\", \"missing\") if p.is_file() else \"missing\")'")"
    HAS_POSTMORTEM=0
    if ssh -n cvm "test -d ~/text-to-cad/outputs/$exp/.codex-upper"; then
        HAS_POSTMORTEM=1
    fi
    IS_FAILED=0
    if [[ "$REMOTE_STATUS" =~ ^[0-9]+$ ]] && [[ "$REMOTE_STATUS" -ne 0 ]]; then
        IS_FAILED=1
    fi
    if [[ "$INCLUDE_BYPRODUCTS" -eq 0 \
        && "$DISCARD_POSTMORTEM" -eq 0 \
        && ( "$HAS_POSTMORTEM" -eq 1 || "$IS_FAILED" -eq 1 ) ]]; then
        echo "  preserving CVM postmortem (final_status=$REMOTE_STATUS, upper=$HAS_POSTMORTEM); skipped" \
            | tee -a "$LOG"
        SKIPPED="${SKIPPED}${SKIPPED:+$'\n'}$exp"
        continue
    fi

    # 1. Upload (CVM 上跑 aws s3 cp)
    # Wrap each pattern in single quotes so CVM's bash treats them as
    # literal strings, not glob patterns. If unquoted, CVM would expand
    # e.g. */.git/* against its cwd (/root/) — the text-to-cad checkout's
    # own .git makes it match real dirs, aws sees them as positional args
    # and bails with "Unknown options: text-to-cad/.git/..." (2026-07-29).
    EXCLUDE_CMD=""
    for pat in "${EXCLUDE_PATS[@]}"; do
        [[ -z "$pat" ]] && continue
        EXCLUDE_CMD+=" --exclude '$pat'"
    done
    # Accept aws exit=2 (warnings only). aws walks the tree first, then
    # applies excludes; if a socket/FIFO tmp file lives inside our
    # excluded dirs (e.g. .codex-upper/tmp/arg0/), the walker warns
    # "Skipping file ... character special device" BEFORE the exclude
    # filter kicks in. Those files are effectively skipped either way
    # so exit=2 (warnings-only) is safe to continue on; only exit >2
    # or exit=1 is a real transfer failure.
    set +e
    ssh -n cvm "aws s3 cp --recursive \
        ~/text-to-cad/outputs/$exp/ $S3_PREFIX/$exp/${EXCLUDE_CMD}" \
        2>&1 | tee -a "$LOG"
    aws_rc=${PIPESTATUS[0]}
    set -e
    if [[ "$aws_rc" -ne 0 && "$aws_rc" -ne 2 ]]; then
        echo "aws s3 cp fatal (exit=$aws_rc) — aborting" | tee -a "$LOG" >&2
        exit "$aws_rc"
    fi

    # 2. Verify file count (CVM local vs S3)。两侧都应用 EXCLUDES 才能对齐。
    if [[ -n "$LOCAL_FILTER_RGX" ]]; then
        LOCAL_N=$(ssh -n cvm "find ~/text-to-cad/outputs/$exp/ -type f | grep -Ev '$LOCAL_FILTER_RGX' | wc -l")
    else
        LOCAL_N=$(ssh -n cvm "find ~/text-to-cad/outputs/$exp/ -type f | wc -l")
    fi
    S3_N=$(ssh -n cvm "aws s3 ls --recursive $S3_PREFIX/$exp/ | wc -l")

    if [[ "$LOCAL_N" -eq "$S3_N" ]]; then
        echo "  verify OK ($LOCAL_N files); cleaning CVM local..." | tee -a "$LOG"
        ssh -n cvm "rm -rf -- ~/text-to-cad/outputs/$exp"
        UPLOADED="${UPLOADED}${UPLOADED:+$'\n'}$exp"
    else
        echo "  VERIFY FAILED (local=$LOCAL_N s3=$S3_N); keeping CVM local. Investigate." | tee -a "$LOG"
        exit 5
    fi
done <<< "$MISSING"

# 全部候选都因失败/postmortem 被保留时，不需要刷新。
if [[ -z "$UPLOADED" ]]; then
    echo "Done. No exp uploaded; preserved postmortem:" | tee -a "$LOG"
    printf '%s\n' "$SKIPPED" | sed '/^$/d;s/^/  /' | tee -a "$LOG"
    exit 0
fi

# --- 让 Mac 立即看到新上传的
# 新 group 必须先刷新其已存在的 parent outputs，再刷新 group，最后刷新 exp。
# 只刷新尚未进入 parent cache 的 group 会返回 "file does not exist"。
REFRESH_WARNING=0
refresh_dir() {
    local dir="$1"
    if ! rclone rc --rc-addr="$RCLONE_RC_ADDR" vfs/refresh \
        dir="$dir" recursive=false >/dev/null 2>&1; then
        echo "warning: rclone refresh failed: $dir" | tee -a "$LOG" >&2
        REFRESH_WARNING=1
    fi
}

refresh_dir "ericzyma/text-to-cad/outputs"
GROUPS_SEEN="$(printf '%s\n' "$UPLOADED" | awk -F/ '{print $1}' | sort -u)"
for group in $GROUPS_SEEN; do
    refresh_dir "ericzyma/text-to-cad/outputs/$group"
done
while IFS= read -r exp; do
    [[ -z "$exp" ]] && continue
    refresh_dir "ericzyma/text-to-cad/outputs/$exp"
done <<< "$UPLOADED"

# Refresh command success alone is insufficient; prove every new exp is visible.
NOT_VISIBLE=""
while IFS= read -r exp; do
    [[ -z "$exp" ]] && continue
    visible=0
    for _attempt in 1 2 3 4 5; do
        if [[ -d "$MOUNT_PATH/$exp" ]]; then
            visible=1
            break
        fi
        sleep 1
    done
    if [[ "$visible" -eq 0 ]]; then
        NOT_VISIBLE="${NOT_VISIBLE}${NOT_VISIBLE:+$'\n'}$exp"
    fi
done <<< "$UPLOADED"

if [[ -n "$NOT_VISIBLE" ]]; then
    echo "S3 upload verified and CVM source cleaned, but mount visibility is pending:" \
        | tee -a "$LOG" >&2
    printf '%s\n' "$NOT_VISIBLE" | sed 's/^/  /' | tee -a "$LOG" >&2
    exit 6
fi

echo "Done. Uploaded + verified + cleaned + mount-visible:" | tee -a "$LOG"
printf '%s\n' "$UPLOADED" | sed 's/^/  /' | tee -a "$LOG"
if [[ -n "$SKIPPED" ]]; then
    echo "Preserved postmortem on CVM:" | tee -a "$LOG"
    printf '%s\n' "$SKIPPED" | sed 's/^/  /' | tee -a "$LOG"
fi
if [[ "$REFRESH_WARNING" -ne 0 ]]; then
    echo "warning: one or more refresh calls failed, but mount visibility checks passed" \
        | tee -a "$LOG" >&2
fi
