#!/usr/bin/env bash
# Push Mac repo → CVM ~/text-to-cad/ via rsync. See .claude/skills/cvm-push/SKILL.md.
#
# 永不加 --delete。改名 / 删文件后 CVM 上残留靠手动 `ssh cvm 'rm ...'` 清。
#
# Usage:
#   scripts/utils/cvm-push.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

# --- 预检 1: cwd 合法性
[[ -f AGENTS.md ]] || { echo "Not at repo root (AGENTS.md not found)" >&2; exit 1; }

# --- 预检 2: CVM reachable + target exists
ssh cvm 'test -d ~/text-to-cad' \
  || { echo "CVM target ~/text-to-cad/ not found" >&2; exit 2; }

# --- 预检 3: CVM 剩余空间
FREE_GB="$(ssh cvm "df --output=avail -BG / | tail -1 | tr -dc '0-9'")"
if [[ "$FREE_GB" -lt 3 ]]; then
    echo "CVM disk too full: ${FREE_GB}G free, need ≥3G. Aborting." >&2
    exit 3
elif [[ "$FREE_GB" -lt 10 ]]; then
    echo "WARN: CVM disk low: ${FREE_GB}G free (threshold 10G)." >&2
fi

# --- log 目标（Monitor tool tail 用）
LOG="${TMPDIR:-/tmp}/cvm-push-$(date +%Y%m%d-%H%M%S).log"
echo "Log: $LOG"

# --- 记录实际 rsync source provenance
# CVM checkout 的 .git 不会随 rsync 更新，因此 remote HEAD 只能说明远端基线，
# 不能代表本次部署内容。linked worktree 和脏 worktree 都是合法 source。
SOURCE_HEAD="$(git rev-parse HEAD 2>/dev/null || echo no-git)"
SOURCE_BRANCH="$(git symbolic-ref --quiet --short HEAD 2>/dev/null || echo detached)"
if [[ -n "$(git status --porcelain --untracked-files=normal 2>/dev/null)" ]]; then
    SOURCE_STATE="dirty"
else
    SOURCE_STATE="clean"
fi
echo "Source: branch=$SOURCE_BRANCH head=$SOURCE_HEAD state=$SOURCE_STATE" \
    | tee -a "$LOG"

# --- 跑 rsync（永不加 --delete）
# Mac /usr/bin/rsync = openrsync (macOS 14+), 不支持 --info=progress2/--stats；
# GNU rsync (brew install rsync) 支持。用 --progress -v 是两者共同子集。
rsync -avz --progress \
    --exclude-from=.cvmignore \
    ./ cvm:~/text-to-cad/ 2>&1 | tee -a "$LOG"

REMOTE_HEAD="$(ssh cvm \
    'cd ~/text-to-cad && git rev-parse HEAD 2>/dev/null || echo no-git')"
echo "Remote Git base: $REMOTE_HEAD (rsync overlay; not deployment identity)" \
    | tee -a "$LOG"
