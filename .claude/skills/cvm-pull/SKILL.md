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

脚本内部固定按六个逻辑模块执行：

```text
Parse request → Discover plan → Qualify terminal/postmortem
→ Publish transaction (upload → verify → cleanup)
→ Expose through mount → Report
```

前 3 个模块只读；`Publish transaction` 是唯一的 S3 写入和 CVM cleanup
模块。每个 exp 必须 verify 成功后才进入 cleanup。

1. 解析 flags：
   - `--include-byproducts`：上传 `run/.codex-upper` 等副产物后才清理失败实验；
   - `--include-byproducts --retain-cvm-source`：上传并验证完整失败实验，刷新
     Mac mount，但保留 CVM 源；用于需要审阅 postmortem 且清理未获授权的情况；
   - `--discard-postmortem`：显式丢弃未上传的 postmortem 后清理失败实验；
   - `--exp <group>/<exp>`：只处理一个 child handle；
   - `--group <group>`：处理一个 batch group；
   - 默认无 scope：扫描全部实验；
   - 失败实验或存在 `run/.codex-upper` 的实验保留在 CVM，不上传、不清理。
   两个 flags 互斥；`--discard-postmortem` 是不可恢复操作，只有用户明确授权丢弃
   本轮列出的失败实验状态时才能使用，不能由 agent 推断。
   `$cvm-monitor` 返回 handle `<group>/<exp>` 时原样传给 `--exp`；`--group` 用于
   显式处理同一 group 下的多个独立 pilot，不依赖 batch job handle。
2. 调脚本：用 Bash tool 跑
   `scripts/pilot/cvm-pull.sh [--exp <handle>|--group <group>] [--include-byproducts [--retain-cvm-source]|--discard-postmortem]`，
   把 `run_in_background` 设为 `true`。记下 log 路径。
3. arm Monitor tool tail log：
   `tail -F <log> | grep -E --line-buffered '(===|verify|Complete|upload:|cleaning|preserving|visible|error|failed)'`
4. 汇报（见 § Handoff）。

## Non-negotiable

- **方案 α — 上传 S3，不写 Mac 本地磁盘**：CVM 用 `aws s3 cp --recursive` 传到
  `s3://arcwm-code-us-west-2/ericzyma/text-to-cad/outputs/<group>/<exp>/`；Mac 通过
  现有 rclone mount `~/threed-code/ericzyma/text-to-cad/outputs/` 看。
- **Method W — 用 S3 prefix 作为恢复提示，不把“存在”当作“完整”**：脚本用
  `rclone lsf threed-code:arcwm-code-us-west-2/ericzyma/... --max-depth 2`
  直读 S3 prefix，不把可能陈旧的 VFS mount cache 当作 source of truth。已有
  prefix 仍必须与 immutable CVM source 验证：完整则恢复 cleanup；少对象则重试
  upload 后再验；多出对象则 fail closed、保留 CVM source。
- **上传后 verify 通过才清理 CVM 本地**：`find CVM local -type f | wc -l` ==
  `aws s3 ls --recursive | wc -l`。**verify fail 保留 CVM local + exit 5**，
  绝不盲删源。
- **失败态默认不清理**：Runner 为非零状态保留 `run/.codex-upper`。默认 pull 检测
  `artifact_manifest.json.final_status != 0` 或 `run/.codex-upper` 后跳过该 exp；
  只有显式 `--include-byproducts` 或 `--discard-postmortem` 才能越过。
- **保留源模式仍须完整验证**：`--retain-cvm-source` 仅能与
  `--include-byproducts` 同时使用。它执行同一 upload、CVM/S3 文件计数验证和 mount
  可见性检查，但跳过 CVM cleanup；已有完整 S3 prefix 仍重新对照不可变 CVM 源。
- **CVM symlink 不得成为上传入口**：upload 固定使用 `--no-follow-symlinks`，CVM
  计数只包含 `lstat` 证明的普通文件。读取 manifest 前，固定 outputs root、group 和
  exp 三层都必须由 `lstat` 证明为真实目录；任一 symlink/缺失/非目录以 exit 7
  拒绝。子树符号链接本身不上传、不计数，也不能把 exp 以外的数据带进固定 S3
  prefix。
- **不得擅自 discard postmortem**：调用 `--discard-postmortem` 前必须向用户列出
  将受影响的失败 exp 并取得明确授权；普通“拉结果”只使用默认安全模式。
- **terminal manifest 是独立硬门**：每个候选都必须有合法且含整数
  `final_status` 的 `artifact_manifest.json`；missing/invalid 时 exit 9，不上传、不
  清理，并回到 `$cvm-monitor`。monitor 返回不能代替 pull 重验。
- **`run/rollout.jsonl` 默认上传**，它是 pilot 的 cost/事故真相源。
- **`run/stderr.log` + `.codex/` + `__pycache__/` 默认排除**；`--include-byproducts` opt-in。
- **rclone mount 必须健康**：跑前直接探测 `127.0.0.1:5572` RC endpoint；
  不依赖 macOS process table，探测失败则 exit 4。
- **不以 mount existence 判完成**：mount 只用于最终 visibility；S3 prefix
  existence 也只是恢复提示，必须与 CVM immutable source 做 count verify。
- **cleanup target 必须是安全的两段相对路径**：不符合
  `[A-Za-z0-9._-]+/[A-Za-z0-9._-]+` 或任一组件为 `.`/`..` 时 exit 7，
  绝不拼进 remote `rm`。

## 边界条件

- 假定 CVM 上 `~/text-to-cad/outputs/` 存在，且 exp 目录按 `<group>/<exp>/`
  两层组织（`toys4k-pilot.sh` 强制该布局）。空或深度不足 2 则脚本
  exit 0（"nothing to do"）。
- 假定 Mac 上 rclone mount `~/threed-code/` 处于 `--vfs-cache-mode full` +
  50G cache，并开放 RC endpoint `127.0.0.1:5572`。
- 假定 CVM AWS creds 属于 `ericzyma`（`~/.aws/credentials`），S3 前缀固定
  `ericzyma/text-to-cad/outputs/`。
- **Pilot dir immutable 假设**（用户 sign-off 2026-07-23）：`toys4k-pilot.sh`
  完成后 exp dir 不再变；杀了重跑生成新 timestamp dir。若某天 pilot 变成可增量
  追加 iter 的模式，此假设失效，method W 会漏掉后加的 iter，需切换到 method Z
  （git SHA 精准）。
- **Empirical 上传速度**（2026-07-23 实测）：CVM→AWS 跨云约 1.5-2 MB/s；
  20MB exp ≈ 10s，60MB 3-pilot ≈ 30-45s。
- **已有 prefix 的恢复**：CVM source 仍在时直接以同一 `--exp` 重跑；
  脚本会验证、补齐 partial upload 或恢复 cleanup，不要求先删 mount/S3。只有
  CVM source 已清且需要主动替换 S3 内容时，才另行请求删除授权。
- **循环内 SSH 必须使用 `ssh -n cvm`**：exp loop 通过 stdin 读取待处理路径；
  普通 `ssh cvm` 会吞掉后续路径，表现为只处理第一个 exp 就提前结束。
- **rclone VFS refresh**：新 group 不能直接 refresh。脚本按
  `outputs parent → group → exp` 顺序做 non-recursive refresh，随后逐个检查 mount
  可见性。S3/cleanup 已成功但 mount 仍不可见时 exit 6，不能打印完整成功。
- **CVM 上传工具**：用 `aws s3 cp --recursive`（`s5cmd` 虽然 DEVCLOUD.md 说该装
  但实际没装；aws cli 够用）。
- **从 mount 只读 SQLite**：使用
  `sqlite3 "file:/absolute/path/run/traces.sqlite3?immutable=1"`；普通打开可能因
  SQLite 尝试创建辅助文件而失败。
- `immutable=1` 只用于 pull 后的 finalized trace；活跃 job 只能由 CVM 侧
  `$cvm-monitor` 以只读 WAL 方式观察。
- Monitor 成功不会自动触发 pull；S3 upload、CVM cleanup 和 discard postmortem
  仍是独立授权边界。Pull 也不删除 `.cvm-jobs/` records。

## Handoff

脚本退出后回给用户：
- 上传的新 exp dir 清单（本轮 uploaded + cleaned）
- 使用 `--retain-cvm-source` 时，分别报告 uploaded + verified + mount-visible 与
  retained-on-CVM 清单；不得称为 cleaned
- 已存在且与 CVM 源计数一致的 S3 prefix 单独报告为 verified-existing，不得称为
  本轮 uploaded；保留源模式不得使用 `resuming cleanup` 文案
- 因失败态/postmortem 默认保留在 CVM 的 exp 清单
- 每 exp artifact 存在性 check（从 mount 侧读）：`workspace.json` /
  `step_index.json` / `notes.md` / `final/manifest.json` / `run/rollout.jsonl`
  各标 ✓/✗；未发布 Final Delivery 时必须说明对应 Workspace 状态
- 下一步提示：`/pilot-review outputs/<group>/`（推荐指向刚上传的整个 group，
  一次审多个 exp；`outputs/` 是 symlink 指向 mount）

失败：
- exit 0 "up-to-date"（S3 里已有全部 CVM exp）→ 只汇报数量，不当失败
- exit 4（RC endpoint 或 S3 remote listing 不可用）→ 分别执行
  `rclone rc --rc-addr=127.0.0.1:5572 core/version` 和带 bucket 的
  `rclone lsf threed-code:arcwm-code-us-west-2/...` 排查
- exit 5（verify fail：本地文件数 ≠ S3 文件数） → 汇报 exp 名 + 两侧计数，指示
  不清 CVM，让用户人工介入
- exit 6（S3 已验证但 mount 尚不可见）→ 明确数据已安全上传，并按 policy 如实
  报告：普通 publish 为 `CVM source cleaned`，`--retain-cvm-source` 为
  `CVM source retained`；刷新 `ericzyma/text-to-cad/outputs` 后重查，不得重跑上传或
  声称数据丢失
- exit 7（unsafe exp path）→ 不上传、不清理，检查 CVM 目录命名
- exit 9（missing/invalid terminal manifest）→ 不上传、不清理；使用同一 handle
  回到 `$cvm-monitor`，不得自动 resubmit
- 单 exp upload 中途失败 → 脚本 `set -euo pipefail` 中止；已成功的 exp 已 verify
  + 已清理（安全）；失败的 exp CVM local 保留

## 如何更新

本 skill 是活的，遇到未覆盖的新情况必须回来改：
- **新副产物出现** / **新 exp 内不该拉的文件** → `.cvmignore.pull` 加行
- **empirical 假设失效**（`run/rollout.jsonl` 变大、开始有 base64 image、新副产物）
  → 本文件 § 边界条件 empirical 段更新；`.cvmignore.pull` 可能要调
- **新的覆盖策略需求**（如某类文件要 backup） → `cvm-pull.sh` 加逻辑 + 本文件
  § Non-negotiable 加对应约束
- **Runner cleanup/postmortem contract 变化** → 同步更新默认保留门，不能让 pull
  静默破坏 Runner 明确保留的诊断状态
- **新的 mount cache 失效模式** → 同时更新 refresh 顺序、exit 语义和可见性测试
- **发现用户用新说法但没触发到 skill** → description trigger phrases 加

commit 消息说明触发事件（例："feat(cvm-pull): pull heatmap-only after run/rollout.jsonl grew to 100MB"）。
参见 `.agents/plans/cvm-sync-and-pilot-review.md § Skill 维护原则`。
