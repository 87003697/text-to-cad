---
name: cvm-pull
description: >-
  Upload CVM new pilot outputs to S3, verify, then clean CVM local.
  Mac reads via existing rclone mount at ~/threed-code/ericzyma/text-to-cad/outputs/.
  Use after a pilot finishes on CVM to reclaim disk and make results visible on Mac.
  Trigger: "cvm-pull", "拉 outputs", "拉 pilot", "拉 CVM pilot",
  "从 CVM 拿 exp", "CVM 跑完拿结果", "sync from CVM".
---

# CVM pull — Tencent DevCloud CVM → S3 (Mac 通过 mount 看)

## 目的

把 CVM 上新的 pilot exp dir 上传到 S3（Mac 通过 rclone mount 自动看到），
上传成功后清理 CVM 本地节省空间。不再往 Mac 本地磁盘写。

**路径布局（2026-07-24 起）**：`outputs/<group>/<exp>/`，group = `YYYYMMDD-HHMMSS-<slug>`。
`<group>/_snapshot/` 是 Mac 端 `snapshot-batch.sh` 产的代码快照，CVM 侧不参与，
本 skill 不管它。

## Workflow

1. 解析 byproducts flag：`$ARGUMENTS` 含 `--include-byproducts` → 透传给脚本。
2. 调脚本：用 Bash tool 跑 `scripts/utils/cvm-pull.sh [--include-byproducts]`，
   把 `run_in_background` 设为 `true`。记下 log 路径。
3. arm Monitor tool tail log：
   `tail -F <log> | grep -E --line-buffered '(===|verify|Complete|upload:|cleaning|error|failed)'`
4. 汇报（见 § Handoff）。

## Non-negotiable

- **方案 α — 上传 S3，不写 Mac 本地磁盘**：CVM 用 `aws s3 cp --recursive` 传到
  `s3://arcwm-code-us-west-2/ericzyma/text-to-cad/outputs/<group>/<exp>/`；Mac 通过
  现有 rclone mount `~/threed-code/ericzyma/text-to-cad/outputs/` 看。
- **Method W — 只上传 S3 里还没有的 exp**：脚本 `find -mindepth 2 -maxdepth 2 -type d`
  列 CVM 上的 `<group>/<exp>`，`comm -23` 过滤掉 mount 里已有，只对差集 upload。
  **依赖 pilot dir immutable 假设**。
- **上传后 verify 通过才清理 CVM 本地**：`find CVM local -type f | wc -l` ==
  `aws s3 ls --recursive | wc -l`。**verify fail 保留 CVM local + exit 5**，
  绝不盲删源。
- **`usage.json` + `rollout.jsonl` 默认上传**（cost 分析 + 事故排查两个用途都要）。
- **`stderr.log` + `.codex/` + `__pycache__/` 默认排除**；`--include-byproducts` opt-in。
- **rclone mount 必须健康**：跑前 `pgrep -f 'rclone mount threed-code'`；不在则 exit 4，不硬跑。
- **不重拉已有 exp**：mount 里已存在 = 完成品；想重拉参见 § 边界条件。

## 边界条件

- 假定 CVM 上 `~/text-to-cad/outputs/` 存在，且 exp 目录按 `<group>/<exp>/`
  两层组织（`toys4k-pilot.sh` 强制该布局）。空或深度不足 2 则脚本
  exit 0（"nothing to do"）。
- 假定 Mac 上 rclone mount `~/threed-code/` 处于 `--vfs-cache-mode full` +
  50G cache（当前 PID 738 参数验证过）。
- 假定 CVM AWS creds 属于 `ericzyma`（`~/.aws/credentials`），S3 前缀固定
  `ericzyma/text-to-cad/outputs/`。
- **Pilot dir immutable 假设**（用户 sign-off 2026-07-23）：`toys4k-pilot.sh`
  完成后 exp dir 不再变；杀了重跑生成新 timestamp dir。若某天 pilot 变成可增量
  追加 iter 的模式，此假设失效，method W 会漏掉后加的 iter，需切换到 method Z
  （git SHA 精准）。
- **Empirical 上传速度**（2026-07-23 实测）：CVM→AWS 跨云约 1.5-2 MB/s；
  20MB exp ≈ 10s，60MB 3-pilot ≈ 30-45s。
- **重拉一个 exp**：`rm -rf $MOUNT_PATH/<group>/<exp>` OR
  `aws s3 rm --recursive $S3_PREFIX/<group>/<exp>/` 后再跑 `/cvm-pull`。若
  CVM local 已清、S3 也删了，就只能靠 pilot 重跑（另一个流程）。
- **rclone VFS refresh**：脚本尾部会跑 `vfs/refresh dir="ericzyma" recursive=false`
  让 Mac 立刻看见。**深层 dir 或 recursive=true 会 fail**（实测）；用 non-recursive
  于父目录才可靠。
- **CVM 上传工具**：用 `aws s3 cp --recursive`（`s5cmd` 虽然 DEVCLOUD.md 说该装
  但实际没装；aws cli 够用）。

## Handoff

脚本退出后回给用户：
- 上传的新 exp dir 清单（本轮 uploaded + cleaned）
- 每 exp artifact 存在性 check（从 mount 侧读）：`notes.md` / `compare_metrics.json` /
  `usage.json` / `rollout.jsonl` / `previews/` 各标 ✓/✗
- 下一步提示：`/pilot-review outputs/<group>/`（推荐指向刚上传的整个 group，
  一次审多个 exp；`outputs/` 是 symlink 指向 mount）

失败：
- exit 0 "up-to-date"（S3 里已有全部 CVM exp）→ 只汇报数量，不当失败
- exit 4（rclone mount 未跑） → 提示 `ps aux | grep rclone` 排查
- exit 5（verify fail：本地文件数 ≠ S3 文件数） → 汇报 exp 名 + 两侧计数，指示
  不清 CVM，让用户人工介入
- 单 exp upload 中途失败 → 脚本 `set -euo pipefail` 中止；已成功的 exp 已 verify
  + 已清理（安全）；失败的 exp CVM local 保留

## 如何更新

本 skill 是活的，遇到未覆盖的新情况必须回来改：
- **新副产物出现** / **新 exp 内不该拉的文件** → `.cvmignore.pull` 加行
- **empirical 假设失效**（rollout.jsonl 变大、开始有 base64 image、新副产物）
  → 本文件 § 边界条件 empirical 段更新；`.cvmignore.pull` 可能要调
- **新的覆盖策略需求**（如某类文件要 backup） → `cvm-pull.sh` 加逻辑 + 本文件
  § Non-negotiable 加对应约束
- **发现用户用新说法但没触发到 skill** → description trigger phrases 加

commit 消息说明触发事件（例："feat(cvm-pull): pull heatmap-only after rollout.jsonl grew to 100MB"）。
参见 `.agents/plans/cvm-sync-and-pilot-review.md § Skill 维护原则`。
