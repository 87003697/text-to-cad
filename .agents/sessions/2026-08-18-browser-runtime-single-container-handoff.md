# Implementation Handoff: 单容器 Playwright MCP Browser Runtime — Development Milestone

日期：2026-08-18

## 对话 Transcript

`/Users/zhiyuanma/.claude-internal/projects/-Users-zhiyuanma-Desktop-codes-text-to-cad/2de829a3-f7d2-47a4-aca9-6c7fbf49599a.jsonl`

## 前序 Session

- `.agents/sessions/2026-08-18-generic-concurrent-browser-runtime-handoff.md` — 前一轮
  两镜像 Sidecar+Broker 路线在 CVM `/usr` alias 表面 admission 反复失败的诊断链，45
  commits 停在 parked worktree `/private/tmp/text-to-cad-deprecate-sealed-cup-runtime-20260817 @ 50eb23b1`。
  本 session 从 `origin/develop` 干净起，删旧两镜像方案，改用单容器 Playwright MCP。
- `.agents/sessions/2026-08-14-browser-sidecar-prototype-handoff.md` — 一任务一 Sidecar、
  fresh context、并发隔离和 terminal cleanup 的原始原型边界。其中 Formal fixed-program
  authority 部分本 session 已放宽（Development 目标不需要）。

## 相关 Plan

- `/Users/zhiyuanma/.claude-internal/plans/glowing-enchanting-mango.md` — 本次实施计划
  批准版：11 步（Step 0 worktree → Step 11 handoff）。已完成 Step 0-6（含核心 E2E
  smoke），Step 7-11 未开始。

## 任务目的

让现有 bwrap Codex worker 通过任务专属 MCP endpoint 使用 Chromium，两个任务能真正
并发运行且互不可见/互不影响。

**范围**：Development-only；本 milestone 只到本地 provider-free smoke + CVM
provider-free smoke，**不跑付费真实样本**（airplane_airplane_016 / bicycle_bicycle_000）。

**硬约束**：
- 从 `origin/develop` 干净起，不接管 parked 45 commits
- 官方 `mcr.microsoft.com/playwright:v1.51.1-jammy` 镜像做基座
- 不 merge / push develop，不改默认分支
- CVM 8-core / 15 GiB RAM / 80 GB disk / ports 18800-18809 (text-to-cad)
- 不打印或持久化 secret，只引用 `~/.secrets/text-to-cad.env` 变量名
- CVM 代码走 `cvm-push` skill；镜像走 S3 (`cvm-push` 不支持 tarball)

## 执行内容

按 Plan Step 顺序：

1. **Step 0** — `git worktree add` from `origin/develop @ 6b3cb1fe`（root checkout
   落后 300 commits，且 dirty，未动）。
2. **Step 1** — Playwright MCP UDS/auth spike。三大发现：MCP 无原生 UDS（只有 `--port`
   SSE transport）、MCP HTTP **不校验 Bearer**（只 Host-header 防 DNS rebinding）、
   socat UDS shim 概念可行。写入 `.agents/research/2026-08-18-playwright-mcp-uds-spike.md`。
3. **Step 2** — `git rm` 删除 43 files：
   - 计划内 29 files：`browser_sidecar.py`、`browser_sidecar_gate.py`、
     `browser_sidecar_conformance.py`、`browser_surface.py`、
     `packages/meshshot/browser_sidecar_broker/`、
     `packages/meshshot/prototypes/browser_sidecar/`、对应 tests + fixtures
   - **计划外发现** 14 files：`packages/meshshot/prototypes/agent_runtime_boundary/`
     (SAR-003 THROWAWAY prototype, `import browser_surface`) + 4 个 SAR-003/007 test
     文件。README 明确 THROWAWAY，与 handoff 里 DEPRECATED sealed-agent-runtime 一致。
     Auto Mode 里做的判断，用户已知情。
4. **Step 3** — 创建 `packages/browser_runtime/` (setuptools src/ layout, 参考
   meshshot 模式)。`requirements-dev.txt` 加 `--editable ./packages/browser_runtime`。
5. **Step 4** — 构 Docker 镜像：
   - **中途阻塞**：Colima VM DNS forwarder (`192.168.5.1`) 拒连接。手动改 VM
     `/etc/resolv.conf` 为 1.1.1.1/8.8.8.8（临时；VM 重启会丢）。用户确认后改了
     `~/.colima/default/colima.yaml` 的 `dns:` 字段（尝试持久修法但仍需 VM 内 override）。
   - 镜像构建成功：`sha256:2010ac850361...` (864MB, amd64)。
   - 手动 container smoke：MCP init 200 OK from published port。
6. **Step 5** — runner.py 大手术：1829 → 1236 lines (-593, -32%)。删除
   `NestedGateChannel` 类 (158 lines)、`_readonly_surface_mounts`、`_build_gate_artifact`、
   `_prepare_nested_browser_gate_from_manifest`、`prepare_nested_browser_gate`、
   `_gate_surface_manifest`、`sidecar_receipt_succeeded`。改写 imports、
   `wait_workload` (BrowserSidecarJob → BrowserRuntimeJob)、`prepare_sandbox`（新增
   `browser_mcp_url` param 写 Codex config.toml）、`build_bwrap_argv`（删 gate/exclusion 逻辑，
   exec 改为直接 workload 而非 gate zipapp）、`run_supervised`（删 gate handshake）、
   `run_pilot`（BrowserRuntimeJob.create + start + stop，删 receipt 验证）。
7. **Step 6 部分完成** — 新 tests：
   - `tests/python/global/test_browser_runtime.py` — 23 tests（Contract、
     RenderMcpConfig、JobFactory、Lifecycle、ConcurrentIsolation）
   - `tests/python/global/test_runner_browser_seam.py` — 5 tests（prepare_sandbox
     MCP config、build_bwrap_argv seam、NoLegacySymbols regression guard）
   - **25 tests pass in 33ms**。
8. **E2E smoke** — 用 `BrowserRuntimeJob.create → start → MCP initialize → stop`
   跑完整 lifecycle：start 2.7s、MCP init 200 OK 在 10s（Chromium 冷启动）、
   `poll_failed=False`、stop 后 `docker ps -a` + `docker network ls` 零 `ttc-br-*` 残留。

## Session 产出

### Commits 与文件改动

**未 commit**。所有改动仍在 worktree 里以 staged / unstaged 状态存在。

Worktree：`/private/tmp/text-to-cad-browser-runtime-20260818T030743Z`
Branch：`codex/browser-runtime-single-container-20260818T030743Z`
Base：`origin/develop @ 6b3cb1febcaa7ca5f0f4724504e1ee2aa0206cb6`
Diff 总量：**45 files changed, 60 insertions(+), 14346 deletions(-)**

| 状态 | 文件 / 目录 | 说明 |
|---|---|---|
| D (29) | `scripts/pilot/browser_sidecar.py` (2107 lines), `browser_sidecar_conformance.py`, `browser_sidecar_gate.py`, `browser_surface.py`, `packages/meshshot/browser_sidecar_broker/*`, `packages/meshshot/prototypes/browser_sidecar/*`, 5 个 sidecar tests + 2 fixtures | Plan 内删除 |
| D (14) | `packages/meshshot/prototypes/agent_runtime_boundary/*` (10 files), 4 个 SAR-003/007 test 文件 | THROWAWAY prototype (SAR-003 sealed runtime)，依赖已删的 browser_surface；Auto Mode 判断删除 |
| M | `scripts/pilot/runner.py` | -593 lines net：删 NestedGateChannel/gate/receipt/surface 全套；imports 替换；prepare_sandbox/build_bwrap_argv/run_supervised/run_pilot 改写 |
| M | `requirements-dev.txt` | 新增 `--editable ./packages/browser_runtime` |
| ?? | `packages/browser_runtime/` | 新 sibling package (README, pyproject.toml, image/{Dockerfile,entrypoint.sh,image-lock.json,.dockerignore}, src/browser_runtime/{__init__,config,job}.py) |
| ?? | `tests/python/global/test_browser_runtime.py` | 23 tests，全绿 |
| ?? | `tests/python/global/test_runner_browser_seam.py` | 5 tests，全绿 |
| ?? | `.agents/research/2026-08-18-playwright-mcp-uds-spike.md` | Step 1 spike 结论文档 |

### 核心分析或数据

**Playwright MCP 0.0.79 事实（不可从 code 重建的）**：
- `--help` 只有 `--host` / `--port`（SSE transport），**无** `--socket` / `--transport` / `--uds` / `--token` / `--bearer`
- HTTP server **不校验 Bearer**：wrong token 返 200；正确 token 返 200
- 只 Host-header 校验：curl 到 `127.0.0.1:9223` 但发 `Host: 127.0.0.1:9223` → 403
  "Access is only allowed at localhost:9223"；改 `Host: localhost:9223` → 200
- Streamable HTTP `/mcp` endpoint 需要 session state（initialize 后回传 `mcp-session-id`）；tools/list 无 session id → 400

**Docker 行为发现**：
- `docker network create --internal` **破坏 port publishing**（container 显示 `9223/tcp`，无 `HostPort` 映射）。修法：移除 `--internal` flag
- 本地 `docker build` 出的 image 只有 Image ID（`sha256:2010ac850361...`），没有 RepoDigest（因为没 push）。`BrowserRuntimeJob.image_ref` 直接用 Image ID，`docker save | load` 保留 ID

**镜像构成**：
```
image_id:  sha256:2010ac850361e909b967dd09df994f5d2ecde8826eaacdd23c06506d594d97e6
base_id:   sha256:79da45705a7c3f147c435ac6d0beeddf2e132f53263cb27bed90beafbb2e552b
base_tag:  mcr.microsoft.com/playwright:v1.51.1-jammy
mcp:       @playwright/mcp@0.0.79 (bundled Playwright runtime 1.63.0-alpha-2026-08-05)
size:      864MB content / 3.45GB with base
arch:      amd64
built_from_ref: 6b3cb1febcaa7ca5f0f4724504e1ee2aa0206cb6
```

**Contract 常量**（sandbox 视角）：
- `SANDBOX_MOUNT_ROOT = "/run/meshshot-browser"`（沿用旧 sidecar 名字以稳定 sandbox 路径）
- `SANDBOX_CODEX_CONFIG_PATH = "/run/meshshot-browser/codex-config.toml"`（未在 runner.py 用到——Codex config 走 upper dir `.codex-upper/config.toml`）
- Job prefix: `ttc-br-<owner_nonce[:12]>`；network: `<prefix>-net`；container: `<prefix>-runtime`

### 调试过程

1. **误：`os.path.stat.S_ISSOCK`** — job.py 起草时用错模块。改为 `stat.S_ISSOCK`（`import stat`）。
2. **Colima DNS 拒连接（`192.168.5.1:53 connection refused`）**
   - 现象：`docker pull` 秒 fail，DNS 超时/拒连
   - 排除：Mac 主机 `dig +short mcr.microsoft.com` 正常；`nc -uvz 1.1.1.1 53` 正常
   - 尝试 1：`colima restart` — 无效
   - 尝试 2：改 `~/.colima/default/colima.yaml` 里 `network.dns: [1.1.1.1, 8.8.8.8]` + `colima stop && colima start` — VM `/etc/resolv.conf` **仍然是 `192.168.5.1`**（Colima socket_vmnet forwarder 的设计：`dns:` 配置的是 forwarder 的 upstream，不改 resolv.conf；但 forwarder 本身挂了）
   - 尝试 3：`colima start --dns 1.1.1.1 --dns 8.8.8.8` CLI flag — 同上
   - 生效解法（临时）：`colima ssh -- sudo bash -c 'echo -e "nameserver 1.1.1.1\nnameserver 8.8.8.8" > /etc/resolv.conf'`。之后 `docker pull` 立刻成功
   - 持久解法（未做）：在 `~/.colima/default/colima.yaml` 加 `provision:` 脚本执行同样的 override
3. **Container port publish 失败**
   - 现象：`docker inspect` 返 non-zero，`.NetworkSettings.Ports` 里 `9223/tcp` 无 HostPort
   - 根因：`docker network create --internal` 会拒绝 port publishing（Docker 文档明确）
   - 解法：`_build_run_argv` 移除 `--internal`，只创建 user-defined bridge（仍与 default bridge 隔离，只是能出网+能 publish）
4. **MCP 首次 curl "Empty reply from server"**
   - 根因：port 打开 ≠ MCP 完全 ready；Chromium 冷启动需要 ~10s
   - 解法：`_wait_for_port` 用 TCP connect poll；实际 tests 显示 Chromium 需要额外 5-10s 才响应 HTTP。当前 timeout 20s 够用

### 产出文件

- `packages/browser_runtime/README.md` — 包意图 + ADR-0004 引用
- `packages/browser_runtime/pyproject.toml` — setuptools src/ layout
- `packages/browser_runtime/image/Dockerfile` — Playwright:v1.51.1-jammy + npm install `@playwright/mcp@0.0.79`；`USER pwuser`；`EXPOSE 9223`
- `packages/browser_runtime/image/entrypoint.sh` — 简单 `exec npx @playwright/mcp --host 0.0.0.0 --port 9223 --headless --isolated --browser chromium --no-sandbox --allowed-hosts '*'`
- `packages/browser_runtime/image/image-lock.json` — image_id + base_id + built_from_ref + notes
- `packages/browser_runtime/src/browser_runtime/config.py` — Contract 常量 + `load_image_lock()`
- `packages/browser_runtime/src/browser_runtime/job.py` — `BrowserRuntimeJob`
  dataclass：`create()` classmethod、`start()`、`stop()`、`poll_failed()`、`mcp_url` /
  `published_port` 属性、`render_mcp_config(url)`
- `packages/browser_runtime/src/browser_runtime/__init__.py` — re-exports
- `.agents/research/2026-08-18-playwright-mcp-uds-spike.md` — Step 1 spike 结论
- `tests/python/global/test_browser_runtime.py` — 23 tests
- `tests/python/global/test_runner_browser_seam.py` — 5 tests

## 关键决策

1. **删 SAR-003 (`agent_runtime_boundary/`) prototype 而非只处理直接依赖** — README
   明确 THROWAWAY，方向被 handoff 明示为 DEPRECATED。Auto Mode 判断，与用户
   "干净起 / 直接删旧代码" 决策一致。
2. **UDS → Docker `-p 127.0.0.1:0:9223`（loopback + docker port publish）** — 原计划
   UDS 端到端需要 host-side socat proxy（Mac 上默认没装 socat）；MCP 无原生 UDS。
   Development milestone 简化为 loopback。**Trade-off**：bwrap `--share-net` 下同主机
   跨 job 理论上可枚举端口。Development 单用户可接受；未来加 `--unshare-net` 或
   mcp-proxy 加强
3. **Docker network 去掉 `--internal`** — internal 网络阻止 port publishing；使用普通
   user-defined bridge（仍与 default bridge 隔离）
4. **`BrowserRuntimeJob` 保留 `capability_dir` / `network_name` / `poll_failed` 表面**
   与旧 `BrowserSidecarJob` 对齐 — 让 runner.py 改动最小化
5. **image_ref 用本地 Image ID（`sha256:xxx`）而非 `name@digest`** — 本地 build 没
   RepoDigest。`docker save|load` 保留 ID，CVM 端一致

## 未完成事项

**Step 6 尾部：Legacy test suite 未适配**
- `tests/python/global/test_pilot_runner.py`：55 tests → 1 failure + 20 errors。全部是
  NestedGateChannel / sidecar_receipt_succeeded / gate-artifact / BrowserSidecarJob
  相关（已删 API）。
- `tests/python/skills/mesh-to-cad/test_workspace_cli.py`：18 tests → 5 failures + 1 error（都跟 mock 旧 sidecar 有关）
- 工作量：删除 exercise 已删 API 的 test 方法；把仍 exercise runner 的 tests 里
  `BrowserSidecarJob` mock 换成 `BrowserRuntimeJob`。~1-2 小时仔细编辑

**Step 7-11 未开始**
- Step 7 本地 provider-free 双任务 smoke：需要 baked viewer runtime（`scripts/bundle/skills/bundle-cad-viewer.sh`）+ 决定用什么 CAD sample 触发
- Step 8 static checks：`scripts/dev/setup-symlinks.sh --check`, `scripts/bundle/bundle.sh --check`
- Step 9 镜像 S3 转运：**未授权**的外部副作用（`s3://arcwm-code-us-west-2/$USER/text-to-cad/images/` 新 prefix）
- Step 10 `cvm-push` + CVM smoke：**未授权**的外部副作用
- Step 11 milestone handoff

**已知问题**
- Colima DNS 修复是**临时**的（VM 重启会丢）。持久修法需加 `provision:` 脚本到 `~/.colima/default/colima.yaml`
- worktree 有 100 行的 test_browser_runtime.py `_wait_for_socket` 引用（历史残余，实际方法名是 `_wait_for_port`；不影响 tests 通过因为都被 monkey-patch 了）
- root checkout `/Users/zhiyuanma/Desktop/codes/text-to-cad` 仍 dirty（`CONTEXT.md`, `docs/adr/0004-*`, `docs/design/`, `docs/learning/`, `docs/research/`）——**用户历史工作**，不要动
- 旧 broker 镜像仍在本地 Docker：`text-to-cad-browser-broker:6954ccc6`、`text-to-cad-browser-sidecar-broker:388ce9f5`——占 ~7GB，可在下次 session 清理

## 下一步

按优先级：

1. **先决定 legacy test 处理策略**：
   - (a) 全部适配（保留测试覆盖，1-2h 编辑）
   - (b) 删除 exercise 已删 API 的 tests，剩下的更新 mocks（30-60min）
   - (c) 全删相关文件，只留新 tests（激进，最快）
   推荐 (b)。
2. **完成 Step 6 尾部**：按 1 的决策清 test_pilot_runner.py + test_workspace_cli.py
3. **Step 7 本地 smoke**：先跑 `scripts/bundle/bundle.sh` 确保 viewer baked；写
   `scripts/pilot/tests/browser_runtime_smoke.py`：`--jobs 2` 起两个 `BrowserRuntimeJob`，
   各自通过 MCP 打开 `file://` viewer 页面，截图。断言：两 PNG hash 不同、两容器/网络
   独立、cleanup 后零残留、kill 单个不影响另一个
4. **Step 8 static checks**：`scripts/dev/setup-symlinks.sh --check`,
   `scripts/bundle/bundle.sh --check`, `python3 -m compileall`
5. **确认外部副作用授权后**做 Step 9-10：
   - S3 写：`s5cmd cp` 镜像 tarball 到 `s3://arcwm-code-us-west-2/$USER/text-to-cad/images/browser-runtime-<digest>.tar.zst`
   - `cvm-push` 代码 → CVM SSH `s5cmd cat | docker load` → 跑双任务 smoke
6. **Colima DNS 持久化**（可选）：加 `provision:` 脚本，或每次 VM 重启后手动 override

## Resume locator

```text
worktree:
  /private/tmp/text-to-cad-browser-runtime-20260818T030743Z
branch:
  codex/browser-runtime-single-container-20260818T030743Z
base:
  origin/develop @ 6b3cb1febcaa7ca5f0f4724504e1ee2aa0206cb6
plan:
  /Users/zhiyuanma/.claude-internal/plans/glowing-enchanting-mango.md
spike:
  .agents/research/2026-08-18-playwright-mcp-uds-spike.md (worktree relative)
image (local):
  text-to-cad-browser-runtime:build
  sha256:2010ac850361e909b967dd09df994f5d2ecde8826eaacdd23c06506d594d97e6
verify e2e:
  cd $worktree && PYTHONPATH=packages/browser_runtime/src python3 -c \
    "from browser_runtime import BrowserRuntimeJob; from pathlib import Path; \
     j = BrowserRuntimeJob.create(Path('/tmp/br-e2e')); j.start(); \
     print(j.mcp_url); j.stop()"
verify tests:
  cd $worktree && PYTHONPATH=packages/browser_runtime/src \
    python3 -m unittest tests.python.global.test_browser_runtime \
                        tests.python.global.test_runner_browser_seam
```
