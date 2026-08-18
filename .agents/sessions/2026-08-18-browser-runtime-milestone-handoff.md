# Implementation Handoff: 单容器 Playwright MCP Browser Runtime — Development Milestone (完结)

日期：2026-08-18

## 对话 Transcript

`/Users/zhiyuanma/.claude-internal/projects/-Users-zhiyuanma-Desktop-codes-text-to-cad/2de829a3-f7d2-47a4-aca9-6c7fbf49599a.jsonl`

## 前序 Session

- `.agents/sessions/2026-08-18-browser-runtime-single-container-handoff.md` — 本 milestone 前半段
  的 mid-session handoff（在 worktree 内，未 merge）。里面记了 Step 0-6 的进度、Colima DNS
  修复、entrypoint executable-path 定位。本文接续 Step 6 尾部到 Step 11 全部完成。
- `.agents/sessions/2026-08-18-generic-concurrent-browser-runtime-handoff.md` — 更早的两镜像
  Sidecar+Broker 路线终止 handoff。本 milestone 从 `origin/develop` 干净起，不接管那 45 commits。

## 相关 Plan

- `/Users/zhiyuanma/.claude-internal/plans/glowing-enchanting-mango.md` — 11-step 实施计划批准版。
  Step 0-10 全部完成，Step 11 (本文档) 交付。

## 任务目的

让现有 bwrap Codex worker 通过任务专属 MCP endpoint 使用 Chromium，两个任务能真正并发运行
且互不可见/互不影响。**范围**：Development-only。本 milestone 只到本地 provider-free smoke +
CVM provider-free smoke，**不跑付费真实样本**。

**硬约束**：
- 从 `origin/develop` 干净起，不接管 parked 45 commits
- 官方 `mcr.microsoft.com/playwright:v1.51.1-jammy` 镜像做基座
- 不 merge / push develop，不改默认分支
- CVM 8-core / 15 GiB / 80 GB / ports 18800-18809
- 不打印或持久化 secret；只引用变量名
- CVM 代码走 `cvm-push` skill；镜像走 S3（`cvm-push` 不支持 tarball）

## 执行内容

前半段（Steps 0-5 + Step 6 核心）由 mid-session handoff 覆盖。本文补 Step 6 尾部到 Step 11：

### Step 6 尾部 — legacy tests 适配

用户批准 hybrid 策略：删除 exercise 已删 API 的 tests，仅 mock 相关的 adapt。**并行 2 sub-agent**：
- Agent A（legacy tests）删 16 tests + adapt 5 tests（`test_pilot_runner.py`, `test_workspace_cli.py`），
  引入 `FakeBrowserRuntimeJob` helper。final 40 + 18 = 58 tests green。
- Agent B（smoke script）写 `scripts/pilot/tests/browser_runtime_smoke.py` (486 → 432 lines final)。

### Step 7 — 本地 provider-free 双任务 smoke（重大迂回）

Agent B 首次 smoke 号称通过一次，但实际是**碰巧**。之后 100% 复现失败：**并发 2 job 时
`browser_navigate` 返 HTTP 200 空 body，单 job 必绿**。

**追根 4 attempt subagent + 7 research subagent 并行**（详见 §关键决策）。

Root cause 定位在 `@playwright/mcp` Streamable HTTP transport 的 5s heartbeat：慢 tool call
（Colima 2 CPU 并发下 9-10s）超时后 server 静默 drop SSE body。**Fix**：
```dockerfile
ENV PLAYWRIGHT_MCP_PING_TIMEOUT_MS=0
```
在 `packages/browser_runtime/image/Dockerfile`。upstream (`playwright-core` commit `4cd8608`,
2026-07-20) 已内置 env kill switch，`@playwright/mcp@0.0.79` 已带。**3 次并发 smoke 全绿**。

### Step 8 — Static checks

- `scripts/dev/setup-symlinks.sh --check` ✅
- `scripts/bundle/bundle.sh --check` ✅（viewer esbuild missing 是 fresh worktree artifact，非致命）
- `python3 -m compileall` ✅
- Unit tests 65 (browser_runtime + seam + pilot_runner) ✅

### Step 9 — 镜像 S3 转运

用户授权后：
```bash
docker save text-to-cad-browser-runtime:build | zstd -T0 | s5cmd pipe \
    s3://arcwm-code-us-west-2/zhiyuanma/text-to-cad/images/browser-runtime-82ab40a22aaf.tar.zst
```
上传 820 MB / 2:49（zstd 对 docker layer 几乎不压缩——layer 本身已 gzip）。

### Step 10 — cvm-push + CVM smoke

- `scripts/pilot/cvm-push.sh --agent` receipt: `status=succeeded head=05e854e1` (~160 KB incremental)
- CVM 端 `aws s3 cp ... | zstd -d | docker load` (15:00 wall time，跨洲慢)
- **Image ID 变了**：Mac `82ab40a2...` → CVM `d274488d...`。Docker save/load 跨引擎不保 content ID。
  手动 patch CVM 侧 `image-lock.json` 的 `id` 到 CVM local sha256。
- 3 次并发 smoke on CVM：**全绿，每次 6.3s（Mac 45-58s）**，PNG hash 跟 Mac 完全一致。

## Session 产出

### 本 session 的 3 个 Commits（worktree `/private/tmp/text-to-cad-browser-runtime-20260818T030743Z`）

| commit | 内容 |
|---|---|
| `bab3d6d5` fix(browser_runtime): stabilize concurrent Playwright MCP smoke | Dockerfile + entrypoint + image-lock（`PLAYWRIGHT_MCP_PING_TIMEOUT_MS=0` + `--executable-path` pin） |
| `ca6db14c` test(pilot): adapt legacy suites to browser_runtime seam | test_pilot_runner.py -1078+82，test_workspace_cli.py +67-4 |
| `05e854e1` test(browser_runtime): add provider-free concurrent smoke script | 新 `scripts/pilot/tests/browser_runtime_smoke.py` (432 lines) |

加上前半段的 3 commits (`09660bb5`, `59dd6cef`, `d5529d8c`)，本 branch 共 6 commits ahead of
`origin/develop`。**未 merge / push develop，等 review**。

### 核心数据

**Image**:
- Local: `text-to-cad-browser-runtime:build` @ `sha256:82ab40a22aafcd3607d4f9249569f684f46584a9941f2e1e17cd9a1d73c58a26` (864 MB content / 3.45 GB with base)
- S3: `s3://arcwm-code-us-west-2/zhiyuanma/text-to-cad/images/browser-runtime-82ab40a22aaf.tar.zst` (820 MB compressed)
- CVM: same tag, ID `sha256:d274488de0e11b84f7e86edafecd196e3aa289088c4b3ac4b68741d5d23bc282` (2.5 GB, load 后 overlay2 内容)

**Smoke 结果**（3 runs each，全绿）:

| Env | Wall/run | Main phase | Kill-safety | PNG sha256 (job 0 / 1) |
|---|---|---|---|---|
| Mac Colima (2 CPU / 4 GiB) | 45-58s | 23-32s | 21-26s | `5747cf49...` / `f148e6b2...` |
| CVM (8 CPU / 15 GiB) | 6.3s | 3.4s | 2.9s | `5747cf49...` / `f148e6b2...` |

PNG hash 完全一致 = 确定性行为。CVM 快约 7-10 倍（CPU 差异）。

### 调试过程摘要

追 concurrent flake 用了 4 + 7 = **11 个 sub-agent** 并行工作：

**4 个 Attempt subagent**（各自 sandbox `/tmp/attempt-*/`，写 fix + 跑 3 次 smoke）：
| Attempt | Fix | Pass |
|---|---|---|
| A. `--ipc=host + --init + --cap-add=SYS_ADMIN` | Playwright docs 推荐 Docker flags | 1/3 |
| D. `--ipc=host + --init` | 纯最小改 | 1/7 |
| C. 单 container N-session（共享 MCP） | Browserless / crawl4ai 生产范式 | 0/3 concurrent（sequential OK） |
| B. 换 base image 到 `node:22-bookworm-slim` + chromium-1237 | 对齐 MCP 官方 image | 0/3 |

**7 个 Research subagent**（各 clone repos 到 `/tmp/research-*/`）：
- P: Steel-browser / Browserless / crawl4ai deploy HTTP API 形状 → **没人用 Streamable HTTP heartbeat**
- Q: 替代 MCP browser server → `tontoko/fast-playwright-mcp` fork（其实就是包 `PLAYWRIGHT_MCP_PING_TIMEOUT_MS` 环境变量）
- **R: heartbeat archaeology → 找到 upstream `PLAYWRIGHT_MCP_PING_TIMEOUT_MS=0` env kill switch（decisive）**
- S: OpenHands / AutoGen / Aider / AutoGPT / Devika 等 agent harness → **他们全部 embed Playwright 直接调，没用 MCP for browser**
- T: Anthropic Computer Use / browser-use / Skyvern / OS-Copilot / Agent-S / E2B desktop → **"MCP is nowhere in the browser/desktop path"**
- U: MineRL / Unity ML / Gymnasium / DeepMind Lab 等 game/RL env → **"reset as a message, not container restart"** + 没人用 heartbeat
- Bonus: Playwright 维护者已把 issue #1646/#1293/#982 **关闭为 "working as designed"**——他们
  的立场是"客户端必须持有 GET /mcp SSE stream 并回 pong"，Codex 客户端做不到就要用 env kill switch

**Root cause 链条**：
1. `browser_navigate` 返 HTTP 200 空 body（100% 并发复现）
2. 单 job 通过，sequential N session on same container 通过 → 不是 container / Chromium / 拓扑问题
3. 空 body 后 same session id 返 404 "Session not found" → server 侧 session invalidated
4. Attempt C 的证据 + 5 research 交叉 → 定位到 heartbeat drop mechanism
5. R 找到 upstream 内置 env kill switch → fix

### 产出文件（新增）

- `packages/browser_runtime/image/Dockerfile` — 加 `ENV PLAYWRIGHT_MCP_PING_TIMEOUT_MS=0`
- `packages/browser_runtime/image/entrypoint.sh` — 加 `--executable-path /ms-playwright/chromium_headless_shell-1161/chrome-linux/headless_shell`
- `packages/browser_runtime/image/image-lock.json` — 新 image sha256
- `scripts/pilot/tests/browser_runtime_smoke.py` — provider-free 双任务 smoke (432 lines, stdlib only)
- `tests/python/global/test_pilot_runner.py` — 40 tests（原 55）
- `tests/python/skills/mesh-to-cad/test_workspace_cli.py` — 18 tests，`SyntheticBrowserRuntime` replaces `SyntheticSidecar`

## 关键决策

1. **接受 heartbeat env kill switch 而非架构改造**：Attempt C（1 container N-session）架构层面
   正确但 heartbeat bug 依然触发；R 的 env var fix 一行 upstream-supported，胜出。
2. **保留 base image `playwright:v1.51.1-jammy`**：Attempt B 证实换 base 不修 concurrent bug；
   node:22-bookworm-slim base 小 37%（547 MB vs 864 MB content）是未来 optimization，不是本
   milestone 关键路径。
3. **不改 runner.py 架构（不上 warm pool）**：Game/RL 世界共识"reset as message, warm pool
   with port offset"更成熟，但对 2-job Development milestone 不必要。记进未来 milestone。
4. **CVM 侧 image-lock 手动 patch**：Docker save/load 跨引擎不保 content ID。写成"Mac ID
   在 lock 里"是 Dev-only 约定；CVM 侧手动改 lock（或未来支持 `image_ref` 用 name:tag fallback）。
5. **`--executable-path` pin 是 hack workaround**：真正的问题是 `@playwright/mcp@0.0.79` 期望
   chrome-for-testing (chromium-1237)，但 `playwright:v1.51.1-jammy` 只有 chromium_headless_shell-1161。
   长期正解是升 base image 或让 MCP 自动 install，本 milestone hack 就够。

## 未完成事项

**未启动**：
- Merge / push develop —— 用户未授权，等 review
- 真实付费样本（airplane_airplane_016 / bicycle_bicycle_000）—— 本 milestone 明确不做
- Warm pool + snapshot resume 架构（Agones/E2B pattern）—— 下 milestone

**已知遗留**：
- **CVM 上 22 个旧 `text-to-cad-cvm-sidecar-broker` 镜像**（约 55 GB），是 parked 分支
  的产物。当前 CVM 70 GB free，没影响本 milestone；下次可清 `docker image prune -a --filter "label=..."` 或按 tag 批量删。
- **Colima DNS 修复仍是临时**（VM 重启会丢）。持久修法：`~/.colima/default/colima.yaml`
  加 `provision:` 脚本，或每次重启手动 override `/etc/resolv.conf`。
- **`viewer/node_modules/.bin/esbuild` 缺失警告**（fresh worktree 未装 viewer 依赖）——
  bundle check 认为非致命跳过。若将来跑 viewer smoke 需先 `npm --prefix viewer ci`。
- `/tmp/attempt-*/` sandbox 目录（4 个，各 20MB）保留作参考。清理无害。
- `/tmp/research-*/` clone 目录（7 个）保留作参考。同上。

**Docker image 生命周期**：
- Mac 本地：`text-to-cad-browser-runtime:build` sha256:82ab40a2...
- S3：`s3://arcwm-code-us-west-2/zhiyuanma/text-to-cad/images/browser-runtime-82ab40a22aaf.tar.zst`
- CVM 本地：`text-to-cad-browser-runtime:build` sha256:d274488d...
- 三处内容等价，ID 因引擎而异

## 下一步

按优先级：

1. **Review + merge decision**（用户）：branch `codex/browser-runtime-single-container-20260818T030743Z`
   6 commits，等 review。合并前建议做的 static check 都通过了。
2. **付费样本 milestone**：真实 airplane/bicycle 样本，需要额外的 Venus 预算和授权。
3. **`--executable-path` hack 消除**：升 base image 或加 install-browser build step，
   让 image 自然带 chromium-1237。（记进 `packages/browser_runtime/README.md`。）
4. **未来 concurrency scale-up**：如果需要真正跑 N > 4 pilot 并发，考虑：
   - Warm pool 预启 N container，allocate on demand（Agones pattern）
   - `--user-data-dir` 每 pilot 一份 + shared MCP（Browserless pattern）
   - snapshot + resume（E2B pattern，激进）
5. **Colima DNS 持久化**（可选）：`provision:` 脚本 or 手动 override 每次重启。

## Resume locator

```text
worktree:
  /private/tmp/text-to-cad-browser-runtime-20260818T030743Z
branch:
  codex/browser-runtime-single-container-20260818T030743Z
base:
  origin/develop @ 6b3cb1febcaa7ca5f0f4724504e1ee2aa0206cb6
commits:
  6 ahead: bab3d6d5 fix -> ca6db14c test -> 05e854e1 test
  (plus 09660bb5 refactor + 59dd6cef feat + d5529d8c chore before mid-session cutoff)
plan:
  /Users/zhiyuanma/.claude-internal/plans/glowing-enchanting-mango.md
image local:
  text-to-cad-browser-runtime:build @ sha256:82ab40a22aafcd3607d4f9249569f684f46584a9941f2e1e17cd9a1d73c58a26
image S3:
  s3://arcwm-code-us-west-2/zhiyuanma/text-to-cad/images/browser-runtime-82ab40a22aaf.tar.zst
image CVM:
  text-to-cad-browser-runtime:build @ sha256:d274488de0e11b84f7e86edafecd196e3aa289088c4b3ac4b68741d5d23bc282
verify locally:
  cd $worktree && for i in 1 2 3; do
    PYTHONPATH=packages/browser_runtime/src python3 scripts/pilot/tests/browser_runtime_smoke.py --jobs 2 --out /tmp/smoke-$i/
  done
verify CVM:
  ssh cvm 'cd ~/text-to-cad && PYTHONPATH=packages/browser_runtime/src python3 scripts/pilot/tests/browser_runtime_smoke.py --jobs 2 --out /tmp/text-to-cad-cvm-smoke/'
unit tests:
  cd $worktree && PYTHONPATH=packages/browser_runtime/src python3 -m unittest \
      tests.python.global.test_browser_runtime \
      tests.python.global.test_runner_browser_seam \
      tests.python.global.test_pilot_runner \
      tests.python.skills.mesh-to-cad.test_workspace_cli
key research clones (Mac /tmp/, keep for reference):
  /tmp/research-heartbeat/playwright-mcp/         (R agent, decisive)
  /tmp/research-alt-mcp/                          (Q agent)
  /tmp/research-computer-use/                     (T agent)
  /tmp/research-harnesses/                        (S agent)
  /tmp/research-deploy/                           (P agent)
  /tmp/research-game-agents/                      (U agent)
  /tmp/research-constrained/                      (前批 chrome-aws-lambda/linuxserver)
```
