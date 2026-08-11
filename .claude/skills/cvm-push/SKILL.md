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

1. 调脚本：用 Bash tool 跑 `scripts/pilot/cvm-push.sh`，把 `run_in_background` 设为 `true`。
   记下 stdout 里的 `Log: ${TMPDIR:-/tmp}/cvm-push-<ts>.log`。
   脚本会把当前 checkout 复制到隔离 staging，在 staging 内物化全部 production
   skill runtime，再把实体 bundle 上传到 CVM；不会修改 source checkout 的开发
   symlink 或共享依赖缓存。稳定 shell 入口只负责调用 `cvm_push.py`；Python workflow
   按 `Preflight → Resolve inputs → Stage → Validate/attest → Transfer →
   Target build → Verify` 编排；传输后在 CVM 目标 ABI 上编译实体 meshscope
   bundle 的 native extension，再用同一份 runtime contract 做本地和远端验收。
2. arm Monitor tool tail log：
   `tail -F <log> | grep -E --line-buffered '(Source:|Building physical|CVM runtime verified|xfer#|sent [0-9]+ bytes|total size|Remote Git base|error|failed|rsync:)'`
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
  `.git`、`.venv`、outputs/models、缓存和构建产物不能成为 deployment material；
  但当前 dirty worktree 的源码必须保留。
- Push 只部署代码；不创建、查询、等待、重试或清理 job。

## 边界条件

- 假定 `ssh cvm` alias 已配（见 `.agents/DEVCLOUD.md`）；未配 → 用户先配。
- 假定 CVM 上 `~/text-to-cad/` 已存在；不做 bootstrap。
- 只做 code push；`models/` 靠 CVM 本地已 hydrate 的 LFS content，不通过 skill 推。
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
- 改名或删除仍不会自动清理 CVM stale path；必须先解析精确目标，再显式删除，
  不能通过 `--delete` 扩大同步权限。

## Handoff

脚本退出后回给用户：
- 传输总量 / speed（从 log 尾 `sent X bytes received Y bytes ... bytes/sec` 段读）
- source branch / HEAD / clean-or-dirty state（从 `Source:` 行读取）
- CVM Git base（从 `Remote Git base:` 行读取，并明确它不是 deployment identity）
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

失败：
- exit 1（cwd 错） → 提示切 repo 根
- exit 2（CVM 目标缺） → 提示 `ssh cvm 'ls ~/'`
- exit 3（磁盘 <3G） → 汇报剩余 GB + 提示 `ssh cvm 'du -sh ~/*'` 清理
- exit 4（production staging 失败）→ 检查 log 中缺失的 Viewer/CAD build dependency、
  bundle command 或残留 skill symlink；此时脚本保证尚未传输任何文件
- exit 5（target-ABI meshscope build/import 失败、远端 runtime 缺失或 attested hash
  不同）→ 不得 submit pilot；比较 staging 与 CVM 的精确 runtime 文件，并检查 CVM
  C++ toolchain 与项目 Python ABI
- exit 6（Playwright browser revision 缺失）→ 在 CVM host 安装脚本报告的 revision，
  随后重新 push
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
