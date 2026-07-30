---
name: cvm-push
description: >-
  Push Mac repo to Tencent DevCloud CVM (~/text-to-cad/) via rsync,
  never --delete. Use before running pilot on CVM.
  Trigger: "cvm-push", "推代码到 CVM", "推 CVM", "同步到 CVM",
  "上代码到 cvm", "CVM 跑前推代码", "sync to CVM".
---

# CVM push — Mac → Tencent DevCloud CVM

## 目的

把当前 Mac checkout 或 linked worktree 的代码增量推到 CVM
`~/text-to-cad/`，用于在 CVM 上跑 pilot。
**不是**通用文件传输，专为 dev iteration loop 的 push 环节。

## Workflow

1. 调脚本：用 Bash tool 跑 `scripts/utils/cvm-push.sh`，把 `run_in_background` 设为 `true`。
   记下 stdout 里的 `Log: ${TMPDIR:-/tmp}/cvm-push-<ts>.log`。
2. arm Monitor tool tail log：
   `tail -F <log> | grep -E --line-buffered '(Source:|xfer#|sent [0-9]+ bytes|total size|Remote Git base|error|failed|rsync:)'`
3. 汇报（见 § Handoff）。

## Non-negotiable

- **永不加 `rsync --delete`**。事故：session 2026-07-23 曾用 `--delete` 抹掉
  CVM `models/toys4k/`。改名 / 删文件后 CVM 上残留靠手动 `ssh cvm 'rm ...'` 清，
  脚本层不做 `--delete`。
- **CVM 磁盘 < 3G 强制中止**（脚本 exit 3）。
- **source 必须是 repo checkout/worktree**（脚本按自身位置解析 repo 根，
  `AGENTS.md` 缺失则 exit 1）。
- **`.git/` 和 `.git` 都不得传输**：前者覆盖普通 checkout，后者覆盖 linked
  worktree 的 gitfile；不得让 rsync 尝试用 gitfile 覆盖 CVM 的 `.git/` 目录。

## 边界条件

- 假定 `ssh cvm` alias 已配（见 `.agents/DEVCLOUD.md`）；未配 → 用户先配。
- 假定 CVM 上 `~/text-to-cad/` 已存在；不做 bootstrap。
- 只做 code push；`models/` 靠 CVM 本地已 hydrate 的 LFS content，不通过 skill 推。
- 一次 skill 调用 = 一次完整同步（无 partial / resume 概念）；rsync 天然增量。
- rsync 不更新 CVM `.git`。CVM HEAD 只是远端 checkout 基线，不是本次部署内容的
  identity；真正的 source provenance 是脚本输出的 branch/HEAD/dirty state。
- 改名或删除仍不会自动清理 CVM stale path；必须先解析精确目标，再显式删除，
  不能通过 `--delete` 扩大同步权限。

## Handoff

脚本退出后回给用户：
- 传输总量 / speed（从 log 尾 `sent X bytes received Y bytes ... bytes/sec` 段读）
- source branch / HEAD / clean-or-dirty state（从 `Source:` 行读取）
- CVM Git base（从 `Remote Git base:` 行读取，并明确它不是 deployment identity）
- 关键运行文件如需严格部署证明，比较 source/CVM SHA-256；不能只引用 CVM HEAD
- 下一步提示（推荐先做 group snapshot 再跑 pilot）：
  ```
  # Mac 端（新 batch 首次）：
  scripts/utils/snapshot-batch.sh <YYYYMMDD-HHMMSS-slug>
  # CVM 端：
  ssh cvm 'cd ~/text-to-cad && ./scripts/pilot/toys4k-pilot.sh <obj> <same-group>'
  ```

失败：
- exit 1（cwd 错） → 提示切 repo 根
- exit 2（CVM 目标缺） → 提示 `ssh cvm 'ls ~/'`
- exit 3（磁盘 <3G） → 汇报剩余 GB + 提示 `ssh cvm 'du -sh ~/*'` 清理
- rsync code 23 且包含 `could not make way for new regular file: .git` →
  检查 `.cvmignore` 同时包含 `.git/` 和 `.git`
- 其他 → 贴 log 尾 20 行

## 如何更新

本 skill 是活的，遇到未覆盖的新事故必须回来改：
- **新的不该 push 的目录 / 文件模式** → 加进 `.cvmignore`
- **新的 rsync 失败模式** → `cvm-push.sh` 加 exit code + 本文件 § Handoff 加对应处理
- **新的预检需求**（如某种网络状态要探）→ `cvm-push.sh` 加预检段 + 本文件 § Workflow 更新
- **公开 pilot 入口迁移** → 同步更新 § Handoff，不保留已删除的运行命令

commit 消息说明触发事件（例："fix(cvm-push): exclude worktrees/ after XX 事故"）。
参见 `.agents/plans/cvm-sync-and-pilot-review.md § Skill 维护原则`。
