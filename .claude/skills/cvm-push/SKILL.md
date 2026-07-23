---
name: cvm-push
description: Push Mac repo to CVM (~/text-to-cad/) via rsync safely.
  Trigger: "cvm-push", "推代码到 CVM", "sync to CVM", "上传", "push code".
---

# CVM push — Mac → Tencent DevCloud CVM

## 目的

把 Mac 主 checkout 的代码增量推到 CVM `~/text-to-cad/`，用于在 CVM 上跑 pilot。
**不是**通用文件传输，专为 dev iteration loop 的 push 环节。

## Workflow

1. 调脚本：用 Bash tool 跑 `scripts/utils/cvm-push.sh`，把 `run_in_background` 设为 `true`。
   记下 stdout 里的 `Log: /tmp/cvm-push-<ts>.log`。
2. arm Monitor tool tail log：
   `tail -F <log> | grep -E --line-buffered '(%|Total|error|failed|rsync:)'`
3. 汇报（见 § Handoff）。

## Non-negotiable

- **永不加 `rsync --delete`**。事故：session 2026-07-23 曾用 `--delete` 抹掉
  CVM `models/toys4k/`。改名 / 删文件后 CVM 上残留靠手动 `ssh cvm 'rm ...'` 清，
  脚本层不做 `--delete`。
- **CVM 磁盘 < 3G 强制中止**（脚本 exit 3）。
- **cwd 必须是 repo 根**（脚本 exit 1 保护）。

## 边界条件

- 假定 `ssh cvm` alias 已配（见 `.agents/DEVCLOUD.md`）；未配 → 用户先配。
- 假定 CVM 上 `~/text-to-cad/` 已存在；不做 bootstrap。
- 只做 code push；`models/` 靠 CVM 本地已 hydrate 的 LFS content，不通过 skill 推。
- 一次 skill 调用 = 一次完整同步（无 partial / resume 概念）；rsync 天然增量。

## Handoff

脚本退出后回给用户：
- Total transferred size / speed（从 log 尾 `--stats` 段读）
- CVM 上 head commit：`ssh cvm 'cd ~/text-to-cad && git rev-parse HEAD 2>/dev/null || echo no-git'`
- 下一步提示：`ssh cvm 'cd ~/text-to-cad && ./scripts/utils/toys4k-pilot.sh <obj>'`

失败：
- exit 1（cwd 错） → 提示切 repo 根
- exit 2（CVM 目标缺） → 提示 `ssh cvm 'ls ~/'`
- exit 3（磁盘 <3G） → 汇报剩余 GB + 提示 `ssh cvm 'du -sh ~/*'` 清理
- 其他 → 贴 log 尾 20 行

## 如何更新

本 skill 是活的，遇到未覆盖的新事故必须回来改：
- **新的不该 push 的目录 / 文件模式** → 加进 `.cvmignore`
- **新的 rsync 失败模式** → `cvm-push.sh` 加 exit code + 本文件 § Handoff 加对应处理
- **新的预检需求**（如某种网络状态要探）→ `cvm-push.sh` 加预检段 + 本文件 § Workflow 更新

commit 消息说明触发事件（例："fix(cvm-push): exclude worktrees/ after XX 事故"）。
参见 `.agents/plans/cvm-sync-and-pilot-review.md § Skill 维护原则`。
