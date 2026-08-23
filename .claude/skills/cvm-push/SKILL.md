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

1. 把代码写入 CVM 是独立外部操作；测试、实现或 review 请求不授权真实 push。取得
   本次 push 的明确授权后再启动命令。
2. 人工直接运行 `scripts/pilot/cvm-push.sh`，保留实时 rsync 进度。Agent 运行
   `scripts/pilot/cvm-push.sh --agent`：详细输出只写本地 log，stdout 是阶段 NDJSON，
   最后一行是 `cvm-push.receipt/1` receipt。
3. 等待同一个 terminal session 或 background task 终止，按 § Long wait 执行。
4. 从 receipt 汇报结果（见 § Handoff）。

脚本会把当前 checkout 复制到隔离 staging，在 staging 内物化全部 production skill
runtime，再把实体 bundle 上传到 CVM；不会修改 source checkout 的开发 symlink 或共享
依赖缓存。稳定 shell 入口只负责调用 `cvm_push.py`；Python workflow 按
`Preflight → Stage → Transfer → Verify → Install` 编排，并用同一份 runtime contract 做本地和远端
验收。

**Install** 阶段把 Mac source provenance、transfer summary 和 runtime attestation 编成
严格 schema 的 canonical JSON/base64url 参数。CVM 在 publication lock 内先检查 Codex
CLI ≥ 0.142.0，再复制已验证的 `~/text-to-cad/`，执行现有 publish-tree finalizer，
通过真实 `codex plugin marketplace add` + `codex plugin add cad@text-to-cad` 安装到
隔离 `CODEX_HOME`，并重算 prepared/installed manifests 与 critical runtimes。全部通过
后才原子替换 `current.json`；相同内容重推幂等。

Pilot 与 `cvm_agent` 从该 authority 深拷贝完整、已注册 plugin 的 `CODEX_HOME` 到每个
job 的私有可写目录。Pilot 把 marketplace source 重写为
`/opt/text-to-cad-publish-tree`，并把已验证 publish tree 只读挂载到该路径；不再用
`~/.codex/skills` 或 loose-skill mounts 代替 plugin 注册状态。

## Long wait

把等待保持为暂停的 orchestration state：每个 quiet interval 只做一次 runtime-max
blocking wait；terminal completion 会提前 hand back。精确的更早 deadline 可以缩短
等待，普通进度不能。

- **Codex**：保留启动命令返回的 terminal session；用空输入 `write_stdin` 等待，内部
  terminal wait 与外层 orchestration yield 都取各自 runtime 支持的最长 interval。
- **Claude**：用 background Bash 启动并保留 task handle；用一个 blocking
  `TaskOutput` 等待，timeout 取 runtime 支持的最大值。

一次工具等待窗口无事件结束不是 push 失败。此时至多做一次只读状态检查；状态未变则
重新进入同样的 long wait。terminal handback 后直接解析 receipt，不做例行状态读取。
用户主动询问、精确 deadline 或真正的 runtime timeout 可以单独唤醒检查。正常路径不
启动 `tail -F`，不循环短 wait，也不周期性读取 log、`ps` 或远端状态。`wait_agent`
只用于 subagent；这里复用其 long-wait 规则，不用它等待 terminal。

## Non-negotiable

- **永不加 `rsync --delete`**。事故：session 2026-07-23 曾用 `--delete` 抹掉
  CVM `models/toys4k/`。改名 / 删文件后 CVM 上残留靠手动 `ssh cvm 'rm ...'` 清，
  脚本层不做 `--delete`。
- **CVM 磁盘 < 3G 强制中止**（脚本 exit 3）。
- **source 必须是 repo checkout/worktree**（脚本按自身位置解析 repo 根，
  `AGENTS.md` 缺失则 exit 1）。
- **`.git/` 和 `.git` 都不得传输**：前者覆盖普通 checkout，后者覆盖 linked
  worktree 的 gitfile；不得让 rsync 尝试用 gitfile 覆盖 CVM 的 `.git/` 目录。
- **`.cvm-jobs/` 与 `.cvm-agent-jobs/` 不得传输**：它们是 CVM 侧权威运行状态，
  Mac 同名目录不能覆盖。
- **CVM skill runtime 永远采用实体 production bundle**：Mac checkout 可以是开发
  symlink；push 不在远端预先 `unlink`，而由经过验证的 staging 作为 rsync source
  完成 symlink→目录转换。
- **一个完整 stage 只做一次远端 rsync**：先构建并验证 Viewer production
  runtime，再应用全局 exclude。不得把主代码和运行依赖拆成两个可能只成功一个的
  网络传输。
- **不得把主 checkout 的依赖目录直接 symlink 进 staging**：Viewer 与 CAD build
  dependencies 必须复制到 staging，避免构建读取错误 worktree 或修改共享缓存。
- **source → staging 必须排除本地状态**：`.agents/`、`.claude/`、`.codex/`、
  `.git`、`.venv`、outputs/models、缓存和构建产物不能成为 deployment material；
  但当前 dirty worktree 的源码必须保留。
- **plugin authority 只在全部 checks 通过后才 publish**：staging 阶段完成 finalize + `codex plugin add` + manifest/critical-runtime 校验以前，不能改动 `~/.text-to-cad-codex/deployments/current.json`；install/verify 任一失败保留旧 pointer。
- **pilot 与 cvm_agent 不再回退到 `~/.codex/skills`**：唯一授权来源是 `current.json` 指向的 deployment `codex-home`；缺失或校验失败必须 fail closed 而非查找旧 symlink。
- Push 只部署代码；不创建、查询、等待、重试或清理 job。

## 边界条件

- 假定 `ssh cvm` alias 已配（见 `.agents/DEVCLOUD.md`）；未配 → 用户先配。
- 假定 CVM 上 `~/text-to-cad/` 已存在；不做 bootstrap。
- 假定 CVM 上 Codex CLI ≥ 0.142.0；版本不足或无法解析在任何 marketplace mutation
  之前 exit 7。
- 只做 code push；`models/` 靠 CVM 本地已 hydrate 的 LFS content，不通过 skill 推。
- linked worktree 缺少 `viewer/node_modules` 或 `tmp/cad-snapshot-build` 时，脚本自动
  查找 primary checkout；仅存在半成品目录不算可用，会继续回退。也可用
  `CVM_PUSH_VIEWER_NODE_MODULES_SOURCE` 和 `CVM_PUSH_CAD_BUILD_DEPS_SOURCE`
  显式指定。自动候选不完整时会继续回退；显式候选不完整则 fail closed，不静默换源。
  依赖只作为 staging 输入，不直接上传 root `node_modules`。
- 一次 skill 调用 = 一次完整同步（无 partial / resume 概念）；rsync 天然增量。
- rsync 不更新 CVM `.git`。CVM HEAD 只是远端 checkout 基线，不是本次部署内容的
  identity；真正的 source provenance 是 receipt 绑定的 Mac branch/HEAD/dirty state、
  transfer summary、runtime attestation 和 content-bound plugin deployment id。
- push 结束前必须按同一 runtime contract 确认 Viewer runtime 是实体目录，
  launcher/backend/dist 存在，并比较 Viewer backend 与 launcher 的 SHA-256。
  任一失败均不得进入 pilot。
- 改名或删除仍不会自动清理 CVM stale path；必须先解析精确目标，再显式删除，
  不能通过 `--delete` 扩大同步权限。

## Handoff

脚本退出后回给用户：
- 传输总量 / speed（receipt `transfer`；rsync 未提供可解析 summary 时为 `null`）
- source branch / HEAD / clean-or-dirty state（receipt `source`）
- CVM Git base（receipt `remote_git_base`，并明确它不是 deployment identity）
- Plugin authority（receipt `plugin_authority`）：`deployment_id`、版本、Codex 版本、
  prepared/installed digest、critical runtimes 和已绑定的 Mac push provenance
- 关键运行文件如需严格部署证明，比较 source/CVM SHA-256；不能只引用 CVM HEAD
- 下一步提示（推荐先做 group snapshot，再 submit + monitor）：
  ```
  scripts/pilot/snapshot-batch.sh <YYYYMMDD-HHMMSS-slug>
  scripts/pilot/cvm-push.sh
  scripts/pilot/cvm-submit.sh pilot <obj> <same-group>
  scripts/pilot/cvm-monitor.sh --wait <returned-handle>
  ```

  To verify installed-plugin discovery without a provider or model inference,
  use the closed provider-free mode after a successful authority publication:
  ```
  scripts/pilot/cvm-submit.sh provider-free installed-plugin <same-group>
  scripts/pilot/cvm-monitor.sh --wait <returned-handle>
  ```

Do not replace submit or monitoring with raw SSH. For strict deployment proof,
compare SHA-256 for `scripts/pilot/cvm_job/`, `toys4k-pilot.sh`, and
`toys4k-batch.sh`; remote Git HEAD is not deployment identity.

失败先读 receipt 并保留它的非零 `exit_code`。只有 receipt 缺失或不足以解释失败时，
才读取一次经过过滤且限长的 log 尾；不把 rsync 进度重新送入模型上下文。

- receipt 缺失且 process 被 `SIGKILL`、机器掉电或解释器崩溃 → 把缺失 receipt 与
  现有 log 作为中断证据；不推断远端完成
- exit 1（cwd 错） → 提示切 repo 根
- exit 2（CVM 目标缺） → 提示 `ssh cvm 'ls ~/'`
- exit 3（磁盘 <3G） → 汇报剩余 GB + 提示 `ssh cvm 'du -sh ~/*'` 清理
- exit 4（production staging 失败）→ 检查 log 中缺失的 Viewer/CAD build dependency、
  bundle command 或残留 skill symlink；此时脚本保证尚未传输任何文件
- exit 5（远端 runtime 缺失或 attested hash 不同）→ 不得 submit pilot；比较
  staging 与 CVM 的精确 runtime 文件
- exit 6（Playwright browser revision 缺失）→ 在 CVM host 安装脚本报告的 revision，
  随后重新 push
- exit 7（Codex 版本 gate、publish-tree finalize 或 `codex plugin marketplace add` /
  `codex plugin add cad@text-to-cad` 失败）→ 看 remote error JSON 的 `error`；
  `current.json` 未变，不 submit pilot
- exit 8（installed plugin cache 与 prepared publish tree 的 manifest/critical runtime 不一致）→ 视为部署 authority 未通过；`current.json` 未变，不 submit pilot
- rsync code 23 且包含 `could not make way for new regular file: .git` →
  检查 `.cvmignore` 同时包含 `.git/` 和 `.git`
- 其他 → 汇报 receipt `phase` / `error`；信息不足时再贴过滤后的 log 尾 20 行

## Validation boundary

默认本地验证可以运行 FakeRunner、临时 receipt/log、wrapper fake module 和临时目录间
的本地 rsync 测试。任何加载正式 `cvm_push.py` 并执行真实 `scripts/pilot/cvm-push.sh`
的集成测试都可能 SSH/rsync 写 CVM，必须取得单独、逐次的明确授权；它不属于默认
test suite，也不授权重试、pilot、S3 或清理。

## 如何更新

本 skill 是活的，遇到未覆盖的新事故必须回来改：
- **新的不该 push 的目录 / 文件模式** → 加进 `.cvmignore`
- **新的 rsync 失败模式** → `cvm-push.sh` 加 exit code + 本文件 § Handoff 加对应处理
- **新的预检需求**（如某种网络状态要探）→ `cvm-push.sh` 加预检段 + 本文件 § Workflow 更新
- **公开 pilot 入口迁移** → 同步更新 § Handoff，不保留已删除的运行命令

commit 消息说明触发事件（例："fix(cvm-push): exclude worktrees/ after XX 事故"）。
参见 `.agents/plans/cvm-sync-and-pilot-review.md § Skill 维护原则`。
