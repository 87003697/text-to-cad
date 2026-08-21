# Browser Sidecar Viewer Asset Host：历史经验与镜像设计护栏

Status: design research  
Date: 2026-08-21  
Scope: `模型数据 → Sidecar Asset Host → baked upstream-compatible CAD Viewer`

## 结论

这条链路值得做，但不应从“再造一个正式 Sidecar 镜像”开始。过去的主要成本并非
Viewer 本身，而是把以下四件事同时放进一次不可重试的镜像迭代：

1. Viewer/模型加载功能验证；
2. Browser 与 Broker 的双镜像身份和权限边界；
3. macOS Colima、native Linux CVM 和 Docker archive 之间的可移植性；
4. Formal Source-Hidden、全文件系统扫描、terminal cleanup 和 proof-only receipt。

历史原型已经证明，真实 Viewer 可以 baked 到 Sidecar，并在 browser-less client 侧通过
`playwright.connect()` 操作；R8 真实加载 STEP/GLB、切换真实投影控件并截图，30/30
predicates 通过。[S4][S5] 后续正式化的失败集中在 Broker/host transport、镜像证明和
surface admission，而不是 Viewer 的 WebGL 渲染。[S3][S9]

因此本设计建议采用 **payload-first, Broker-image-last, Sidecar-unchanged**：

- 先生成一个可逐字节验证的 `Viewer Broker Payload`（baked `viewer/dist`、无依赖 Asset
  Host、manifest 和一个 GLB fixture）；
- 先在现有浏览器基座上证明同源 `/__cad/*` 加载协议和真实 Viewer ready；
- 只有 payload 验收通过后，才用一次离线、路径 allowlist 的镜像 assembly 把它封入
  browser-less Broker；既有 exact Sidecar 继续只提供已验证的 Playwright Server + Chromium，
  不因 Viewer/catalog/Asset Host 迭代而重建；
- 应用验证阶段不构任何镜像；payload 冻结后只构建一次新的 Broker image，不恢复旧的完整
  Linux filesystem surface scanner，也不修改 CAD Viewer React/cadjs 代码；Formal
  registered-program authority 在同一个 Broker 边界上作为后续独立层接入。

这不是降低最终安全目标，而是把“功能事实”和“部署/证明事实”拆成可独立失败、可独立复用
的 Gate。ADR 仍要求最终 Formal 路径无 source mount、无外网、无 runtime pull/download、
固定 Render Program、fresh context/page 和 fail-closed cleanup。[S1][S2]

## 研究边界与证据权重

本报告只使用仓库的一手材料：当前 ADR/spec、实现 handoff、Git 中的原型/生产源码、测试、
Dockerfile 和 image lock。历史 handoff 中的结论只有在能被对应源码、receipt 摘要或提交
记录支持时才作为事实使用。

必须区分三代实现：

| 代际 | 用途 | 关键身份 | 当前地位 |
| --- | --- | --- | --- |
| Throwaway Browser Sidecar prototype | 验证真实 Viewer、residual、隔离和 cleanup | Sidecar source `1abe4c9`; R8 docs successor `ef8fc8c5` | 成功证据，不应直接生产化 |
| Formal Sidecar + browser-less Broker | 固定 program authority 和 sealed evidence | Sidecar `1abe4c9`; Broker implementation `091b9d3` | 历史实现已从 develop 删除 |
| `packages/browser_runtime` | Development-only 通用 Playwright MCP | current `image-lock.json` | 仍存在，但明确不满足 Formal fixed-program authority |

`1abe4c9` 与 `091b9d3` 是两个不同 artifact 的 source identity，不能把后者当作前者的“新版
Sidecar source”。当前 Formal spec 也分别记录 Sidecar image source revision 和 Broker OCI
revision。[S2][S3]

## 历史上已经证明的事实

### 1. 真实 Viewer baked 进 Sidecar 是可行的

`1abe4c9` 的 prototype Dockerfile 使用固定 Playwright child digest，在 build stage 编译真实
`viewer/dist`，在 runtime stage 放入 Viewer server/source、fixture、cadjs/cadpy package 和
Chromium。client 打开 `/?file=browser_sidecar_inspection.step`，查找真实 projection control，
用键盘操作真实 Radix menu，并验证 ARIA 从 Orthographic 变为 Perspective。[S5]

R8 的结构化结果记录：Viewer screenshot 60,743 bytes；真实 STEP/GLB 无 artifact error；
projection transition 成功；同一稳定 Playwright connection 下每个 program 新建/关闭 fresh
context/page；并发隔离与 terminal cleanup 同时通过。[S4]

结论：本轮不需要先发明新的 Viewer 页面，也不需要先改 `CadViewer`。真正未知的是“任意
request-scoped 模型 bytes 是否能经兼容 Asset Host 进入同一份 Viewer”，不是“Viewer 能否
在 Sidecar 中运行”。

### 2. 固定浏览器版本和实际 executable 必须作为一个整体验证

原型固定 Playwright 1.60.0、Chromium revision 1223 和官方 `linux/amd64` child digest。
[S4][S5] 当前 Development Browser Runtime 则固定 `playwright:v1.51.1-jammy`，但
`@playwright/mcp@0.0.79` 默认期望另一套 Chrome-for-Testing；实际镜像只有
`chromium_headless_shell-1161`，最终不得不通过 `--executable-path` 指定真实存在的二进制。
[S10][S11]

结论：不能把“镜像里有 Chromium”和“调用方使用的 Playwright 会找到这个 Chromium”视为
同一事实。这里反而是复用既有 exact Sidecar 的重要理由：它已经把 Playwright 1.60.0、
Chromium revision 1223 和 executable 路径作为一套验证过的 artifact 冻结。若将来确实替换
Sidecar，新的 candidate 才必须在 image Gate 中同时证明：

- Playwright package version；
- 浏览器 executable 的固定绝对路径；
- executable 文件 digest 或浏览器 revision/version；
- 一次离线 launch + page load；
- runtime 不触发浏览器查找、安装或下载。

### 3. Viewer fixture 必须包含 Viewer 真正消费的完整 artifact graph

原型 R5 使用 STL，无法出现 STEP inspection control；R6 换成 STEP 后，Viewer 正确报告隐藏
generated GLB 缺失；R7 补齐 STEP/GLB 后又遇到 Locator actionability timeout；R8 最终通过
真实控件 focus + keyboard activation 成功。[S4]

结论：fixture 不能只代表“有几何”。对于当前第一阶段，最安全的是只承诺一个自包含 GLB，
synthetic catalog 也只暴露一个 GLB entry。STEP、topology sidecar、package 和 selector graph
应在后续以显式 asset roles 扩展，不能暗中假定 Viewer 会从 STEP 自行产生浏览器可用资产。

### 4. 同一 request 内的模型身份必须绑定“解析后的 bytes”，不能绑定偶然的文本表示

CVM 首次完整 probe 已经成功启动浏览器、建立一个 context/page、证明无 source alias 和
外网，但最后仍失败：sealed result hash 的是 stdin 精确 bytes，而调用端对 JSON 重新序列化
后的 bytes 做比较。后续修正只改变 request-byte binding，第二次 probe 才成功。[S7]

结论：`ViewerDocument` 需要明确区分：

- transport bytes digest（用于 wire/receipt）；
- decoded asset bytes digest（用于模型身份）；
- canonical metadata digest（如确实需要）。

不要让 JSON whitespace、key order 或 base64 spelling 成为“模型是否相同”的判据。正式 V0
不使用 base64：closed JSON header 后紧跟 exact-length binary frame；Broker 流式计算 GLB
SHA-256 和 byte length。

## 失败模式：为什么过去这么贵

### A. 镜像迭代与 Formal 证明耦合，任何小修都产生新 artifact identity

Formal contract 绑定 exact image ID、platform、source revision、base identity、program digest
和 sealed Gate artifact；任何 Dockerfile-copied source 改动都需要重建、重新锁定、重新检查
和重新 review。[S2][S3]

历史 handoff 记录了大量 superseded Broker images。仅 exact-image aggregate 就多次耗时约
114–131 秒，另有两次在 180 秒达到 discovery timeout；0.5 CPU 的完整 image scan 是直接
原因，改为 discovery-only 1 CPU 才能稳定完成约 99 秒。[S3]

针对新 Asset Host 的含义：在 catalog mapping、MIME、ready probe 仍在探索时就重建 Formal
image，会把本可毫秒级/秒级复测的应用错误升级为两分钟级镜像验证和新身份审查。

### B. 生产 Broker 从 browser image 继承，扩大了 surface 与证明成本

历史 Broker Dockerfile 从 prototype browser-less Agent image 派生，复制五个 pilot module 和
整个 `meshshot` package，并对 `/etc /home /opt /run /srv /usr /var` 删除 dangling symlink；
image test 又逐文件抽取 Dockerfile-copied source，与对应 Git revision 做 byte parity。
[S12]

随后真实 Ubuntu surface 暴露了跨 root symlink、alternatives round-trip、`/etc/mtab`、
`/run/shm`、X11 compatibility alias、私有 home、权限和 `/dev/shm` 等大量非产品语义问题。
这些问题有合理的 Formal 背景，但与“Viewer 能否加载一份 GLB”无关。[S3]

针对新 Asset Host 的含义：不要为了第一张真实 Viewer 图片恢复完整 Broker image 或扫描
整个基础发行版。第一阶段的可信对象应是一个很小的 payload manifest，而非“基础镜像中
所有文件都符合浏览器表面规则”。

### C. host-shared filesystem 被当成 Unix socket/bind transport，跨环境失败

Colima production-shaped conformance 先因 daemon 不共享 `/private/tmp`/home bind source 失败；
后来即使配置 mode-0700 shared root，Unix socket 在 host-shared filesystem 上 bind 仍返回
`Errno 95 Operation not supported`。macOS `/var` 与 `/private/var` canonical alias 又导致过一次
bind source 失败。相同 Unix socket transport 在 native Linux CVM 才通过。[S3]

针对新 Asset Host 的含义：不要重建已经验证的 Browser Sidecar。Asset Host 与 baked Viewer
放入 browser-less Broker，Browser 通过现有 Docker internal network 访问一个固定 Broker
HTTP origin；Agent→Broker 仍走 job-private capability。Unix socket 和 request data 都只位于
Docker-native Broker tmpfs/volume，不能位于 host-shared directory。Colima host-shared UDS 的
`Errno 95` 已经证明，不能用 native Linux 成功推断 macOS shared filesystem 可行。

### D. 本地 Docker image ID 不是可靠的跨 daemon artifact identity

CVM provision 先后暴露：`docker image load` 后不能假设原始 Mac/Colima config ID 在目标 daemon
中仍可按相同 ID 寻址；普通 image inventory 还会漏掉 untagged parent；后来改成 role-specific
reference → loaded ID，并在目标 daemon 重新 attestation。[S7][S8]

Development Browser Runtime 的后续记录也明确显示 Mac image ID `82ab...` 经 archive 到 CVM
后变为 `d274...`，当时只能手工 patch CVM lock。[S10]

针对新镜像的含义：供应链必须区分：

- source/build manifest digest；
- OCI archive digest + size；
- destination role reference；
- destination loaded image ID/config digest；
- runtime container 实际 image identity。

不能把 Mac local image ID 写成唯一跨环境 authority。最终 runner 应消费 provision receipt
中的目标端 identity，而非继续引用构建机 lock 中的 ID。

### E. Docker build context 会悄悄带入 mutable/unreviewed bytes

历史第一个 full-context Broker build 被 byte-parity extraction 拒绝，因为 working-tree bytecode
进入镜像。修正方案是从 exact committed revision 生成 path-allowlisted `git archive`，使用
`--pull=false --no-cache --network=none` 构建，并逐一提取 Dockerfile-copied paths 与 Git bytes
比较。[S2][S3][S12]

当前仓库又明确规定 development layout 使用 symlink，而 production trees 不能包含 symlink；
CAD Viewer bundle script 也强调由 committed `package-lock.json` 决定 package manager，并提供
fresh bundle check。[S13]

针对新 payload 的含义：镜像 build context 不能直接是 repo root，也不能 `COPY viewer`、
`COPY packages` 或 `COPY node_modules`。必须先生成无 symlink、无 cache、闭包明确的 payload，
再让 Dockerfile 只 `COPY payload/`。

### F. request size/memory budget 靠真实模型才会暴露

两镜像路线在进入真实 depth-8/production geometry 后，连续经历 measured payload、request
上限、Broker memory admission、full-geometry budget 和最终 bounded float32/uint32 buffer
streaming；对应 `094c626b → dec10a44 → 9d3b191e → fa23ffc2 → d4908a04 → e465dc36`，随后每次
又生成新的 image-lock commit。[S9][S14]

针对 ViewerDocument 的含义：不要先猜一个 JSON/base64 上限再构镜像。第一阶段必须先测量
airplane GLB 的：raw bytes、base64/wire bytes、decode 峰值、Asset Store 峰值、Viewer load
峰值和 total request time。正式传输优先 exact-length binary frame 或 bounded spool；即使 V0
暂用 base64，也必须在构镜像前冻结 measured limit 和 headroom。

### G. 网络、readiness 和 transport 有跨层陷阱

Development 单容器路线发现：Docker `--internal` network 与 host port publishing 冲突；TCP
port open 不等于 MCP ready；两任务慢调用超过 5 秒时，Playwright MCP heartbeat 会静默丢掉
Streamable HTTP body，最终通过 `PLAYWRIGHT_MCP_PING_TIMEOUT_MS=0` 修复。[S10]

这些不是 Asset Host 必然要继承的问题。新链路应避免 host-published Viewer port和 MCP
heartbeat：Browser 只在既有 Sidecar 中运行；Viewer/Asset Host 只在 Broker 的 Docker-internal
fixed origin 暴露；Agent 只使用既有受控 Broker transport。ready 必须是 Viewer/model-level
signal，而不是“端口已监听”或首页返回 200。

### H. “一次性 no-retry”让未知问题的探索代价指数放大

Formal/CVM 流程把每个 handle 视为 terminal one-shot，失败后不得 retry/adopt/clean；每轮需要
新 source identity、prepare/provision/probe receipt 和授权。[S2][S7][S8] 这对正式证据是合理
的，却不适合发现 catalog 字段、GLB route、CSS readiness 或 Viewer load behavior。

针对新设计的含义：所有应用层未知项必须在进入 Formal one-shot Gate 前通过本地可重复
prototype。正式候选只验证已经冻结的 payload，不承担接口探索。

## 可直接复用的成熟做法

以下做法已经有一手实现或测试证据，应该复用而非重写：

1. **Outer-owned lifecycle**：一任务一 Sidecar；一个 job 可复用稳定 browser connection；每个
   operation fresh context/page；所有 owned resource 在 terminal path 清理。[S1][S4]
2. **Fixed no-fallback selection**：正式 authority 存在但 malformed/unreachable 时 fail closed，
   不在 Agent 内启动或安装 Chromium。[S2]
3. **Exact structured request**：拒绝 URL、JavaScript、path、endpoint、browser/Docker args 和
   unknown keys；限制 body size；duplicate JSON key 拒绝。[S2][S8]
4. **Fresh ownership and cleanup precedence**：只按 Docker create 返回且 label 验证过的 exact
   ID 获取 cleanup authority；cleanup/retained-resource failure 优先于普通 workload success。
   [S2][S3]
5. **Offline image construction**：fixed base、no pull、no cache、network none、allowlisted clean
   context、Dockerfile-copied byte parity。[S2][S3][S12]
6. **Role-specific remote provisioning**：archive hash/size、role reference、loaded identity、目标端
   re-attestation、`--pull=never` 和 bounded receipt。[S7][S8]
7. **Real control verification**：不要 direct React/Three state mutation；使用真实 UI semantics 或
   一个稳定、通用的 Viewer ready/observation seam。[S4][S5]
8. **Viewer build closure discipline**：committed lockfile 决定 package manager；bundle freshness；
   production payload 不含 development symlink。[S13]

## 针对 Asset Host + baked upstream Viewer 的推荐设计

### 1. 冻结 Sidecar，只新增可分层证明的 Broker payload/artifact

```text
Existing exact Browser Sidecar
  └─ Playwright Server + Chromium (unchanged)

Viewer Source Identity
        │ deterministic build
        ▼
Viewer Broker Payload
  ├─ viewer/dist/**
  ├─ asset-host.mjs
  ├─ fixture.glb
  └─ payload-manifest.json
        │ offline assembly
        ▼
Browser-less Broker OCI Artifact
        │ provision receipt
        ▼
Destination Runtime Identity
```

`payload-manifest.json` 至少绑定：

- schema version；
- upstream Viewer commit/release identity；
- repo integration commit；
- committed `viewer/package-lock.json` digest；
- Node/build-tool identity；
- 每个 payload regular file 的 relative path、size、SHA-256；
- tree digest；
- Asset Host program digest；
- fixture GLB digest；
- explicit `symlinks: 0`；
- explicit `sourceMaps` policy。

Broker image lock 再绑定 payload tree digest、exact browser-less base digest、entrypoint digest
和 target platform；Browser executable identity 继续由不变的 Sidecar lock 独立绑定。目标端
provision receipt 分别绑定 Sidecar role 与 Broker role 的 archive/reference/loaded identity。
这些 identity 不得互相替代。

### 2. Asset Host 应是 dependency-free、Broker-owned、同源、request-scoped

V0 Asset Host 使用 Node core `http` 即可，不引入 Express、Viewer Node server 或完整
`node_modules`。它只在 Sidecar+Broker internal network 上提供一个固定、不可由 caller
选择的 origin：

```text
GET /                         baked viewer/dist SPA
GET /assets/<baked-name>      baked viewer static assets
GET /__cad/server             fixed capability response
GET /__cad/catalog            one synthetic GLB entry
GET /__sidecar/assets/<sha256>.glb
```

模型 store 以 request 为生命周期，key 是 decoded bytes SHA-256。大模型不应同时保留
JSON string、base64、decoded bytes 和 Viewer response 四份内存：Agent→Broker 使用 exact-
length binary framing，Broker 边读边 hash 并写入 bounded per-request tmpfs file；Asset Host
再从该文件流式响应。tmpfs 有单请求和 job 总量 hard limit，cleanup 删除 exact owned file。
响应固定 MIME、exact content-length、`Cache-Control: no-store`，未知 method/path/digest 全部
拒绝。Browser context route 再拒绝除该 fixed Broker origin 外的所有 request，作为 external
request census；不能依赖页面“应该不会出网”。

V0 不复用 local filesystem backend，也不复制 upstream Python/Node server。兼容 seam 是
Viewer 已消费的 `/__cad/*` observable protocol，而不是 server implementation。

### 3. 第一版 ViewerDocument 只承诺一个自包含 GLB

```text
ViewerDocumentV0
  schema
  documentDigest
  entry { id, kind="glb", label, primaryAsset }
  assetHeader { mediaType, byteLength, sha256 }
  binaryFrame[byteLength]
```

规则：exact keys；一个 entry/asset；固定 media type；exact-length binary framing；raw/wire limits；GLB magic、version、
declared length 检查；拒绝 external buffer/image URI；不接受 caller path、URL、filename、HTML、
JS、Viewer query string、browser args。Synthetic filename 由 Broker 固定生成，与 caller 数据
无关。

不要在 V0 引入 STEP、topology、URDF、multi-file package、semantic command 或 snapshot
option。它们需要不同 asset graph 和 readiness，历史 R5/R6 已证明混在第一轮会掩盖根因。

### 4. ready 是应用事实，不是基础设施事实

第一阶段按以下顺序报告阶段化 timing：

```text
host_listening
viewer_document_loaded
catalog_hydrated
asset_requested_and_digest_matched
viewer_model_loaded
first_webgl_frame
proof_screenshot_captured
cleanup_complete
```

Prototype 可先使用 DOM/canvas/loading/alert 的组合探针，但必须记录它依赖的 observable。若
不稳定，只向 Viewer 增加与 Sidecar 无关的通用 `viewer-ready` event/state；不加入
`if (sidecar)` UI 分支，也不创建 Sidecar 专用 Viewer 页面。

### 5. 第一阶段先做无镜像应用验证，再做一次 Broker candidate

第一步完全不构镜像：在本机以真实 built `viewer/dist`、Asset Host 和现有浏览器运行应用级
smoke，反复修正 catalog/asset/readiness，直到 airplane 稳定加载。这个阶段只验证 HTTP/
Viewer 语义，不声明 Source-Hidden 或 Formal evidence。

接口与 payload 冻结后，只构建一次新的 Broker candidate：

```text
existing exact Sidecar (unchanged)
  └─ fixed Chromium/Playwright

one new Broker candidate
  ├─ fixed registered program
  ├─ baked viewer/dist
  ├─ fixed Asset Host
  └─ bounded tmpfs request store
```

外部 prototype runner 通过 job-private Broker socket 提交 header + exact-length binary asset；
Browser 通过 internal network 读取固定 Broker origin。两者都不需要 host port；截图作为
bounded result 返回。

这一步只回答“模型数据能否进入真实 Viewer”。成功后再决定：

- registered-program policy 在 Broker 内如何分层；
- atomic scenario 还是短 session；
- semantic commands；
- `cad snapshot` 接入。

### 6. 唯一的新 Broker 镜像必须是 assembly 结果，不是 build workspace

推荐两步：

1. 用固定 builder identity 和 committed npm lock，在仓库工作流中生成 payload；执行 Viewer
   test/build、payload manifest 校验、symlink=0、unexpected files=0。
2. 用极小 Dockerfile 从 exact browser-less Broker base digest 开始，只 `COPY payload/` 和
   fixed entrypoint；`--pull=false --no-cache --network=none`。Sidecar image lock不变。

禁止：

- `COPY .`、`COPY viewer`、`COPY packages`；
- 把 repo `node_modules` 复制进 runtime；
- image build 时访问 npm；
- runtime `npx --yes` 或 `playwright install`；
- image 内保存 source checkout、Git metadata、models directory；
- production payload 中出现 symlink；
- 使用 floating base tag 作为最终 identity。

如果 Viewer build 必须在容器完成，builder stage 也要以 digest 固定、只输入 manifest 声明的
source closure，并将 runtime stage 的输入收敛成 payload；不能重复 prototype Dockerfile 那种
把 Viewer server、完整 packages 和 node_modules 全部带入 runtime 的形状。[S5]

## 建议的实施 Gates

### Gate 0 — 冻结环境事实，不构镜像

- 选定 exact upstream-compatible Viewer revision；
- 选定 airplane 的最终自包含 GLB，记录 bytes/digest；
- 记录当前目标 Sidecar base 里真实 browser executable/version；
- 明确 V0 non-goals；
- 测量 raw/base64/request/peak-memory budgets。

Fail condition：GLB 非自包含、Viewer build closure 不确定或浏览器/Playwright 不匹配。

### Gate 1 — Payload reproducibility

从两个 clean temporary trees 生成 payload，要求 tree digest 一致；manifest 中无绝对路径、
mtime authority、symlink、cache、source map 泄漏（除非显式允许）或 node_modules。运行 Viewer
现有 tests/build 与 bundle freshness check。[S13]

Fail condition：任何 nondeterministic file、不同 package manager/layout 或 working-tree bytes
进入 payload。

### Gate 2 — Host protocol compatibility，不构正式镜像

使用真实 baked `viewer/dist` + dependency-free Asset Host，在可重复本地环境加载 airplane：

- catalog 被同一 Viewer 接受；
- asset 只请求一次且 digest 相符；
- 外部 request 为零；
- real Viewer workbench + viewport 可见；
- screenshot 和阶段 timing 可取；
- success/failure 都销毁 store。

Fail condition：需要修改 Viewer UI 才能表达 Sidecar，或必须给 caller URL/path。

### Gate 3 — Local exact-Sidecar + one-new-Broker candidate

保持 exact Sidecar ID 不变；从 exact browser-less base + accepted payload 离线 assembly 一个
Broker candidate。先运行 Broker image inventory/launch test，再跑 airplane。这里只做可重复
prototype，不消耗 Formal no-retry handle。

Fail condition：Sidecar 被重建、runtime 下载、source mount、host port、host-shared socket、
非 fixed Broker origin、tmpfs budget 越界或 cleanup residue。

### Gate 4 — Negative and resource tests

至少覆盖 digest mismatch、length mismatch、invalid GLB、external URI、oversize、unknown route、
Viewer timeout、browser crash、client disconnect。每条都要证明 browser work 是否开始、page/
context/store/container 的 terminal disposition，以及错误大小有界。

另外用 airplane 实测两次，确认不是 cold-cache 偶然成功；再用两个不同 GLB concurrent 运行，
证明 asset store 不串数据。

### Gate 5 — 才进入 Formal image/policy integration

此时再版本化 `viewer` Render Program、更新 program/image identities、接入 Browser Authority
和 fail-closed Gate。Formal build 使用 clean allowlisted archive、offline assembly、copied-byte
parity 和目标端 provision receipt。[S2][S3][S8]

首次 Formal candidate 不应同时引入 semantic commands 或 `cad snapshot`。固定 operation 只做
`load-one-glb-and-prove-real-viewer`，因此若失败，仍能定位是 artifact/provision、transport、
Asset Host、Viewer ready 还是 cleanup。

## 代码改动边界

第一阶段建议新增或生成：

- 一个独立的 dependency-free Broker Asset Host module；
- `ViewerDocumentV0` validator 和 bounded exact-length binary frame reader；
- payload builder + manifest verifier；
- local functional runner；
- fixed candidate entrypoint；
- protocol、negative、real-Viewer integration tests；
- prototype result JSON 和 proof screenshot（临时输出，不作为正式 evidence）。

第一阶段明确不修改：

- `CadWorkspace` / `CadViewer` / `cadjs`；
- upstream local filesystem backend；
- Viewer 本地启动流程；
- `cad snapshot` / `mesh-preview`；
- current Formal `browser_contract.json` / Browser Authority；
- existing production image lock；
- current Agent-facing API。

只有 Gate 2 证明现有 Viewer 缺少稳定 ready fact 时，才考虑一个与 Sidecar 无关、也可服务
Viewer E2E 的通用 readiness seam。

## 不应复用的旧形状

1. **不要恢复完整两镜像 branch 作为起点。** 该路线最终 45 commits 被 parked，历史 handoff
   明确指出 4,000+ 行主要服务 Formal authority/receipt，而非通用浏览器功能；develop 后来在
   `d5529d8c` 删除旧 Sidecar/Broker/prototypes。[S9][S10][S15]
2. **不要从 prototype browser-less Agent image 派生 Broker/Asset Host。** 它把无关发行版
   surface 和浏览器包历史带入证明范围。[S12]
3. **不要把 Viewer source/server/node_modules 全放进 runtime。** 只 bake verified dist 与小
   Host payload。[S5]
4. **不要先把 airplane bytes 塞进既有 residual JSON contract。** 历史 geometry budget 链已
   证明这会反复触发 JSON object graph 和 Broker memory问题。[S9][S14]
5. **不要把 host port、host-shared Unix socket 或 Mac temp path当正式 transport。** 三者都
   有过实际失败或隔离不足记录。[S3][S10]
6. **不要把 local image ID 当跨环境 digest。** archive/source/role-reference/loaded identity
   必须分层。[S7][S8][S10]
7. **不要用“HTTP 200/port open”代表 Viewer ready。** 历史 MCP 冷启动与 heartbeat 都证明
   transport readiness 不等于应用完成。[S10]

## 最小决策记录

建议把当前方向冻结为：

> 先在无镜像应用 smoke 中构建和验证一个 deterministic Viewer Broker Payload；冻结后只
> 构建一次新的 browser-less Broker image，既有 exact Browser Sidecar 不重建。Broker-owned、
> dependency-free、request-scoped Asset Host 通过 upstream Viewer 已消费的同源 `/__cad/*`
> 协议暴露一份经 binary framing 写入 bounded tmpfs 的自包含 GLB；真实 baked Viewer 运行在
> Sidecar Chromium 中并产生 proof screenshot。Formal V1 不包含 semantic commands 或
> cad snapshot。

这样既保留 ADR 的最终安全边界，也将过去最昂贵的 image/provision/review 循环推迟到模型
数据链路已经被重复证明之后。

## Primary sources

**[S1]** [`docs/adr/0004-own-provider-free-browser-lifecycle-by-authority.md`](../adr/0004-own-provider-free-browser-lifecycle-by-authority.md)，尤其 Decision / Consequences。  
**[S2]** [`docs/specs/browser-sidecar-formal-pilot-integration.md`](../specs/browser-sidecar-formal-pilot-integration.md)，尤其 Public seams、Fixed artifact identity、Adversarial matrix、Evidence and interruption rules。  
**[S3]** [`docs/specs/browser-sidecar-formal-pilot-handoff.md`](../specs/browser-sidecar-formal-pilot-handoff.md)，尤其 Exact artifact identities、Deterministic verification、Preserved production-shaped conformance attempts、Deferred interaction。  
**[S4]** [`.agents/sessions/2026-08-14-browser-sidecar-prototype-handoff.md`](../../.agents/sessions/2026-08-14-browser-sidecar-prototype-handoff.md)，以及 Git primary artifact `ef8fc8c5:packages/meshshot/prototypes/browser_sidecar/HANDOFF.md`。  
**[S5]** Git primary source `1abe4c97929906b5c0b28b0f3f38857bd923952f`：`packages/meshshot/prototypes/browser_sidecar/{Dockerfile,server.mjs,client.mjs,contract.mjs}`。复核：`git show 1abe4c9:<path>`。  
**[S6]** [`.agents/sessions/2026-08-15-cvm-browser-sidecar-provision-probe.md`](../../.agents/sessions/2026-08-15-cvm-browser-sidecar-provision-probe.md)。  
**[S7]** [`.agents/sessions/2026-08-15-cvm-sidecar-inspect-diagnostic.md`](../../.agents/sessions/2026-08-15-cvm-sidecar-inspect-diagnostic.md)，尤其 Portable image-address、Loaded inventory、Role-reference loaded-ID、request-byte successor、successful paid CVM closure。  
**[S8]** 同 [S6]，尤其 prepare/provision/probe 的 exact archive、role identity、bounded receipts 与 no-retry contract。  
**[S9]** [`.agents/sessions/2026-08-18-generic-concurrent-browser-runtime-handoff.md`](../../.agents/sessions/2026-08-18-generic-concurrent-browser-runtime-handoff.md)，尤其 parked 45-commit chain、geometry transport 与 complexity diagnosis。  
**[S10]** [`.agents/sessions/2026-08-18-browser-runtime-milestone-handoff.md`](../../.agents/sessions/2026-08-18-browser-runtime-milestone-handoff.md) 和 [single-container handoff](../../.agents/sessions/2026-08-18-browser-runtime-single-container-handoff.md)，尤其 executable mismatch、heartbeat、network/port、CVM image-ID 和 timing。  
**[S11]** [`packages/browser_runtime/image/Dockerfile`](../../packages/browser_runtime/image/Dockerfile)、[`entrypoint.sh`](../../packages/browser_runtime/image/entrypoint.sh)、[`image-lock.json`](../../packages/browser_runtime/image/image-lock.json) 和 [`README.md`](../../packages/browser_runtime/README.md)。  
**[S12]** Git primary source `091b9d3b95f2b7797c1cac9414f05439923a439c`：`packages/meshshot/browser_sidecar_broker/{Dockerfile,Dockerfile.dockerignore,image-lock.json}`、`tests/python/global/test_browser_sidecar_broker_image.py`、`tests/python/fixtures/browser_sidecar_image_harness.py`。复核：`git show 091b9d3:<path>`。  
**[S13]** [`scripts/bundle/skills/bundle-cad-viewer.sh`](../../scripts/bundle/skills/bundle-cad-viewer.sh)、[`scripts/dev/skills/setup-cad-viewer-skill-symlink.sh`](../../scripts/dev/skills/setup-cad-viewer-skill-symlink.sh) 和 repo `AGENTS.md` production-no-symlink rule。  
**[S14]** Git primary commit chain `094c626b`, `dec10a44`, `9d3b191e`, `fa23ffc2`, `d4908a04`, `e465dc36`；复核：`git show --stat <sha>` 并查看对应 `browser_contract.json`、`renderer.py`、`browser_sidecar.py` diff。  
**[S15]** Git primary source `d5529d8c`（删除 sealed Sidecar+Broker 和 throwaway prototype）、`59dd6cef`（引入 single-container Browser Runtime）、`09660bb5`（runner rewire）。复核：`git show --stat <sha>`。
