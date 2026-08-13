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

push 的远端 preflight 会在 staging/transfer 前闭合验证 CVM 项目 `.venv` 中的 Python
distribution 是精确 `playwright==1.60.0`，其 package manifest 中恰有一个
`chromium-headless-shell` revision `1223` entry，并冻结现有 rev-1223 executable 的
SHA-256；部署后再验证同一 identity 且 executable 摘要未变化。任何不匹配以 exit 7
停止，不能继续 submit。

修复这个单一依赖是独立的 CVM 写操作，必须另行取得明确授权。获授权后只运行无参数
入口 `scripts/pilot/cvm-sync-playwright-runtime.sh`；它没有 package/version/command
输入，只会在 CVM 项目 `.venv` 中同步固定 `playwright==1.60.0`（`--no-deps`），不会
执行 `playwright install`、清理 outputs 或修改 job/repo state。不要改用 raw SSH pip
命令。stdout 只有一个 `cvm-playwright-runtime-sync.receipt/1`，公开字段仅包含 schema、
固定 requested identity、before/after 闭合 match 状态和 exit status；远端原始输出、
路径、环境与凭据不得进入 receipt。若现有 rev-1223 executable 身份无法先证明，sync
会在 pip 前 fail closed。

脚本会把当前 checkout 复制到隔离 staging，在 staging 内物化全部 production skill
runtime，再把实体 bundle 上传到 CVM；不会修改 source checkout 的开发 symlink 或共享
依赖缓存。稳定 shell 入口只负责调用 `cvm_push.py`；Python workflow 按 `Preflight →
Resolve inputs → Stage → Validate/attest → Transfer → Target build → Verify` 编排；传输后
在 CVM 目标 ABI 上编译实体 meshscope bundle 的 native extension，再用同一份 runtime
contract 做本地和远端验收。

Stage 按确定顺序递归展开 `skills/` 与 `plugins/cad/skills/` 内的嵌套开发 symlink，
达到无链接的 fixed point 后才执行 bundle。每个 target 都须解析在隔离 stage 内；
broken、external、cyclic 或 collision 状态统一以 exit 4 结束 staging。

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
- **`.cvm-jobs/` 不得传输**：它是 CVM 侧权威运行状态，Mac 同名目录不能覆盖。
- **CVM skill runtime 永远采用实体 production bundle**：Mac checkout 可以是开发
  symlink；push 不在远端预先 `unlink`，而由经过验证的 staging 作为 rsync source
  完成 symlink→目录转换。
- **一个完整 stage 只做一次远端 rsync**：精确 include implicit runtime 的四个
  `node_modules` 依赖，再应用全局 exclude。不得把主代码和运行依赖拆成两个可能只
  成功一个的网络传输。
- **meshscope native 必须按目标 ABI 构建**：rsync 后由 CVM 项目 Python 对实体
  `skills/mesh-compare` bundle 执行 `build_ext --inplace`，远端验收再从该 bundle
  import `_native`。Mac Darwin binary 和 host editable install 都不是 production
  evidence。
- **不得把主 checkout 的依赖目录直接 symlink 进 staging**：Viewer 与 CAD build
  dependencies 必须复制到 staging，避免构建读取错误 worktree 或修改共享缓存。
- **source → staging 必须排除本地状态**：`.agents/`、`.claude/`、`.codex/`、
  `.git`、`.venv`、outputs、缓存和构建产物不能成为 deployment material；
  但当前 dirty worktree 的源码必须保留。唯一的 models 例外是
  **provider-free durable fixture allowlist**：
  `models/simple/rectangular_clamp_block.py` 和
  `models/simple/simple_model_library.py`。它们必须作为普通文件进入同一 stage、哈希
  attestation 和 deployed-source authority；任何其他 `models/` 路径仍保持排除。
- **docs version metadata 只服务于 stage build**：source copy 单独恢复
  `docs/package.json` 和 `docs/package-lock.json`，供 production bundle 做 version
  sync；staging → CVM 仍排除完整 `docs/`，因此它们不是 deployed-source 或 runtime
  authority。
- Push 只部署代码；不创建、查询、等待、重试或清理 job。
- Push 和固定 runtime sync 都不会安装浏览器；rev-1223 cache 缺失或变化必须停下，
  不能用 `playwright install` 自动补齐或覆盖。

## 边界条件

- 假定 `ssh cvm` alias 已配（见 `.agents/DEVCLOUD.md`）；未配 → 用户先配。
- 假定 CVM 上 `~/text-to-cad/` 已存在；不做 bootstrap。
- 除上述两个 provider-free durable fixtures 外，`models/` 靠 CVM 本地已 hydrate
  的 LFS content，不通过 skill 推。
- linked worktree 缺少 `viewer/node_modules` 或 `tmp/cad-snapshot-build` 时，脚本自动
  查找 primary checkout；仅存在半成品目录不算可用，会继续回退。也可用
  `CVM_PUSH_VIEWER_NODE_MODULES_SOURCE` 和 `CVM_PUSH_CAD_BUILD_DEPS_SOURCE`
  显式指定。自动候选不完整时会继续回退；显式候选不完整则 fail closed，不静默换源。
  依赖只作为 staging 输入，不直接上传 root `node_modules`。
- 一次 skill 调用 = 一次完整同步（无 partial / resume 概念）；rsync 天然增量。
- rsync 不更新 CVM `.git`。CVM HEAD 只是远端 checkout 基线，不是本次部署内容的
  identity；真正的 source provenance 是脚本输出的 branch/HEAD/dirty state。
- push 结束前必须按同一 runtime contract 确认 Viewer/implicit/meshscope runtime
  是实体目录，launcher/backend/dist/snapshot、四个 implicit dependency 文件和
  meshscope native source 存在；比较关键 source/runtime SHA-256，从实体 meshscope
  bundle import target-ABI `_native`，并确认 host cache 中有对应 Chromium revision。
  任一失败均不得进入 pilot。
- Viewer deployment 必须输出 `cvm.viewer-runtime-deployment/1` receipt，逐项记录
  canonical source、generated bundle 和 CVM deployed runtime 的路径与 SHA-256；bundle
  identity stale、deployed digest 不同或 native backend identity 不是
  `meshscope.voxblame.native-sat/1` 都 fail closed。
- 改名或删除仍不会自动清理 CVM stale path；必须先解析精确目标，再显式删除，
  不能通过 `--delete` 扩大同步权限。

## Handoff

脚本退出后回给用户：
- 传输总量 / speed（receipt `transfer`；rsync 未提供可解析 summary 时为 `null`）
- source branch / HEAD / clean-or-dirty state（receipt `source`）
- CVM Git base（receipt `remote_git_base`，并明确它不是 deployment identity）
- 保留 receipt `deployed_source_authority`；它承载 deployed source、native runtime、
  portable Workspace authority 和 review evidence
- 保留 receipt `viewer_deployment`；其中的 `cvm.viewer-runtime-deployment/1` 是
  source/bundle/deployed Viewer runtime identity 的精确交接证据
- 关键运行文件如需严格部署证明，比较 source/CVM SHA-256；不能只引用 CVM HEAD
- 下一步提示（推荐先做 group snapshot，再 submit + monitor）：
  ```
  scripts/pilot/snapshot-batch.sh <YYYYMMDD-HHMMSS-slug>
  scripts/pilot/cvm-push.sh
  scripts/pilot/cvm-submit.sh pilot <obj> <same-group>
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
- exit 5（target-ABI meshscope build/import/identity 失败、远端 runtime 缺失、Viewer
  source/bundle/deployed receipt 不一致或 attested hash 不同）→ 不得 submit pilot；比较 staging 与 CVM 的精确 runtime 文件，并检查 CVM
  C++ toolchain 与项目 Python ABI
- exit 7（Python Playwright version/package manifest 不匹配，或 rev-1223 executable
  缺失/变化）→ 不得 submit；若且仅若已有浏览器 executable 身份可证明，另行授权运行
  固定 `scripts/pilot/cvm-sync-playwright-runtime.sh` 后重试 push。浏览器缺失/变化不能
  由此命令修复，也不能运行 `playwright install`
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
