# Agent-sandbox browser options

Date: 2026-08-14

Status: Decision input adopted by
[ADR 0004](../adr/0004-own-provider-free-browser-lifecycle-by-authority.md).
This note is research evidence, not the current runtime contract.

## Question

Has somebody already built a browser that an agent can use reliably inside a
sandbox, and can this repository adopt it instead of owning a large custom
browser lifecycle runtime?

## Executive answer

Yes, the industry has solved several large parts of this problem:

- Playwright provides version-coupled browser binaries, browser automation,
  an official container image, and a remote browser-server protocol.
- Browserless packages browsers as a self-hosted service with sessions,
  queues, timeouts, health checks, and Playwright/CDP connections.
- Browserbase and Cloudflare Browser Run provide managed browser sessions.
- E2B provides agent sandboxes and desktop computer use; its current cloud-
  browser guidance deliberately runs the browser outside the agent sandbox.

No reviewed first-party product promises the complete repository-specific
contract: provider-free and offline execution, an exactly attested browser
tree, no source alias visible from any renderer-visible process root, outer
lifecycle ownership, no nested `Popen`, closed bounded evidence, and positive
proof that all owned processes, mounts, sockets, profiles, and trees were
removed.

That does **not** imply that all of the current custom implementation is
necessary. The strongest simplification is to move browser materialization,
identity, isolation, and termination to the **outer job runtime**:

1. build a Playwright + Chromium image ahead of time;
2. pin it by OCI image digest;
3. start a browser sidecar before the agent;
4. give the browser no source mount and no external network;
5. make its root filesystem read-only and give it only bounded temporary
   storage;
6. let the agent connect through a local Playwright endpoint;
7. let the outer job/container runtime stop and account for the entire browser
   process tree.

This is an inference from the primary sources below. It would replace much of
the per-job copy/mount/process-cleanup machinery with established container or
microVM lifecycle machinery. A small repository adapter would still be needed
to translate outer-runtime facts into the existing closed evidence schema.

## Four different problems that are easy to conflate

### 1. Stable browser automation

Playwright is already the standard answer here. Each Playwright release expects
specific browser binaries, offers a hermetic install under
`node_modules/playwright-core/.local-browsers`, and warns that arbitrary
`executablePath` versions are not guaranteed to work. See Playwright's
[browser installation and hermetic-install documentation](https://playwright.dev/docs/browsers)
and [`BrowserType` API](https://playwright.dev/docs/api/class-browsertype).

Playwright can also launch a browser server and allow a client to attach with
`browserType.connect()`. Its documentation requires the connecting and serving
Playwright major/minor versions to match. This is a higher-fidelity connection
than generic CDP; Playwright explicitly says `connectOverCDP()` is lower
fidelity and warns that launching Chromium without Playwright's curated
arguments can break functionality. See
[`browserType.connect`, `connectOverCDP`, and `launchServer`](https://playwright.dev/docs/api/class-browsertype).

Conclusion: this repository should not own browser-version selection,
dependency installation, or a generic CDP compatibility layer unless a harder
constraint forces it to.

### 2. A browser running in a sandbox

Playwright publishes an official Docker image containing its browsers and OS
dependencies. Its Docker guide recommends `--init` to reap zombie processes,
version pinning, and a non-root user plus a seccomp profile when visiting
untrusted pages. It also says the image is intended for testing/development and
is not recommended as-is for untrusted websites. See the official
[Playwright Docker guide](https://playwright.dev/docs/docker).

Docker already exposes the relevant primitives: an init process that forwards
signals and reaps processes, read-only root filesystems, private PID/IPC/network
namespaces, bounded stop-to-`SIGKILL`, and `--pull=never` for offline execution.
The `none` network driver creates only loopback. See Docker's
[`docker container run` reference](https://docs.docker.com/reference/cli/docker/container/run/)
and [`none` network driver](https://docs.docker.com/engine/network/drivers/none/).
Docker can pin an image by immutable digest; its documentation says this
guarantees the same image version is selected. See
[`docker image pull`: pull by digest](https://docs.docker.com/reference/cli/docker/image/pull/#pull-an-image-by-digest-immutable-identifier).

Chromium is itself a multi-process system. On Linux, its zygote initializes ICU,
NSS, V8 snapshots and shared libraries, establishes namespace sandbox state,
and forks later renderers. Chromium explains that binary/shared-library version
skew is unsupported, which is why it preserves a version-correct zygote rather
than simply executing a path repeatedly. See Chromium's
[Linux zygote design](https://chromium.googlesource.com/chromium/src/+/main/docs/linux/zygote.md).
Chromium's Linux sandbox uses namespaces plus seccomp-BPF and may place target
processes in empty PID/network namespaces and a read-only root. See the current
[Chromium Linux sandbox source documentation](https://chromium.googlesource.com/chromium/src.git/+/refs/heads/main/sandbox/linux/README.md).

Conclusion: the browser's process and resource-tree complexity is real, but an
outer container/microVM runtime is a more natural owner than application-level
Python code inside the agent job.

### 3. Remote browser as a service

Several products already make browser lifecycle somebody else's problem:

- Browserbase defines a session as one isolated browser instance in its cloud,
  creates it through an authenticated Sessions API, and returns a connection
  URL for the caller's automation framework. It supplies session termination,
  recordings, logs, and live inspection. See Browserbase's
  [session documentation](https://docs.browserbase.com/platform/browser/getting-started/create-browser-session)
  and [observability documentation](https://docs.browserbase.com/platform/browser/observability/observability).
- Cloudflare Browser Run executes headless Chrome on Cloudflare's global
  network and supports direct Playwright, Puppeteer, CDP, or Stagehand sessions.
  See the official [Browser Run overview](https://developers.cloudflare.com/browser-run/)
  and [Playwright integration](https://developers.cloudflare.com/browser-run/playwright/).
- E2B's browser guide combines an E2B agent sandbox with Kernel cloud browsers,
  but is explicit that the cloud Chromium instance runs on Kernel's managed
  infrastructure, **not inside the sandbox**, and that the agent reaches it over
  CDP, BiDi, or a computer-use API. See E2B's
  [cloud-browser guide](https://e2b.dev/docs/use-cases/remote-browser).

Conclusion: managed browser services solve availability, sessions, scale, and
observability. They do not meet a no-provider/no-egress run. Their architecture
does, however, reinforce the idea that the agent should consume a narrow
browser connection while another authority owns the browser.

### 4. This repository's stronger provider-free proof contract

The current contract is not simply "launch Chromium successfully." It requires:

- no runtime downloads or provider calls;
- exact browser-tree identity;
- a read-only Browser Execution Tree;
- Source-Hidden execution, including no source alias reachable through another
  visible process's `/proc/<pid>/root`;
- a browser process tree owned outside the nested renderer;
- no nested process-launch authority exposed to the agent;
- a closed, bounded, non-sensitive evidence vocabulary;
- terminal cleanup proof, including precedence for positively retained
  resources.

None of the product documentation reviewed above advertises these as one public
contract. Browser service session IDs, logs, timeouts, and container exit codes
are useful operational evidence, but they are not equivalent to the repository's
browser-image and retained-resource receipts.

This gap is expected: the repository is asking for a formal, portable proof
about a browser that must run without trusting or contacting a browser provider.
Most browser products assume either that the local container image is trusted or
that the managed service is the trusted authority.

## Product comparison against the actual contract

| Option | Stable automation | Runs with an agent sandbox | Provider-free/offline | Exact local image authority | Source-Hidden by product contract | Outer cleanup ownership | Closed repository evidence |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Playwright library + hermetic browser | Yes | Yes | Yes after pre-install | Version-coupled files, but no repository receipt | No | No | No |
| Official Playwright container/server | Yes | Yes | Yes if preloaded and `--pull=never` | Can be strengthened with pinned image digest | Not by default; achievable by mount topology | Container runtime | No |
| Self-hosted Browserless | Yes | Yes, normally as a sidecar/service | Yes after image provisioning | Can be strengthened with pinned image digest | Not by documented contract | Browserless + container runtime | No |
| Browserbase | Yes | Agent connects remotely | No | Provider-owned | Provider-owned, not locally provable | Provider-owned session | No |
| Cloudflare Browser Run | Yes | Agent connects remotely | No | Provider-owned | Provider-owned, not locally provable | Provider-owned session | No |
| E2B Desktop sandbox | Interactive computer use | Yes | Not in hosted use | Template/sandbox identity, not browser-tree receipt | Not documented | E2B sandbox lifecycle | No |
| E2B + Kernel cloud browser | Yes | Browser explicitly outside sandbox | No | Provider-owned | Provider-owned, not locally provable | Provider-owned session | No |
| Current custom runtime | Targeted to one formal render | Yes | Yes | Yes | Intended, current issue must still prove it | Application code | Yes |

## The best self-hosted packaged option: Browserless

Browserless is the closest ready-made browser **service** that can run on owned
infrastructure. Its open-source image exposes Playwright/Puppeteer-compatible
connections. Its Enterprise Docker deployment adds queueing, concurrency,
timeouts, reconnection bounds, health checks, metrics and private deployment.
See Browserless's [official repository](https://github.com/browserless/browserless),
[self-hosting overview](https://docs.browserless.io/enterprise/docker/overview),
and [configuration reference](https://docs.browserless.io/enterprise/docker/config).

It is attractive when multiple agents need a shared browser pool or when
queueing, live debugging, recordings, and fleet health are product needs. For a
single fixed eight-view residual render per job, it may add more Interface and
licensing surface than needed. The official repository is dual-licensed under
SSPL-1.0 or a Browserless commercial license and says proprietary commercial/CI
use requires a commercial license; that must be reviewed before adoption.

Browserless still would not eliminate the need to:

- pin and approve the image/browser revision;
- ensure the browser sidecar cannot see a source mount;
- deny external network access;
- bind the session to one experiment;
- translate session/container termination into closed repository evidence;
- independently verify there is no retained container, profile volume, socket,
  or session.

## The agent-sandbox products: E2B

E2B is evidence that the broader "stable environment for an agent" problem is
well developed. Its Desktop SDK offers an Ubuntu desktop, VNC streaming,
screenshots, input actions, command execution, timeouts, and explicit sandbox
termination. See E2B's
[computer-use guide](https://e2b.dev/docs/use-cases/computer-use) and
[sandbox lifecycle](https://e2b.dev/docs/sandbox).

Its open infrastructure separates a control plane from a node orchestrator that
owns Firecracker microVMs, networking, and storage; templates are pre-booted VM
snapshots with copy-on-write root filesystems. See E2B's official
[infrastructure architecture](https://github.com/e2b-dev/infra/blob/main/docs/ARCHITECTURE.md).
Self-hosting therefore exists, but it is a platform adoption requiring KVM,
Firecracker, orchestration, storage, and networking—not a small browser library.

For this repository, E2B is a useful architecture reference, not the first
dependency to add. It supports moving identity and lifecycle ownership outward
to a job orchestrator. It does not supply the repository's formal browser
receipts without custom integration.

## Recommended decision

### Adopt: outer-owned, pinned Playwright browser sidecar

Use the smallest off-the-shelf building blocks rather than a managed browser
provider or a new browser fleet:

```text
outer CVM job authority
  ├─ verifies preloaded OCI image digest
  ├─ starts browser sidecar first
  │    ├─ pinned Playwright + bundled Chromium
  │    ├─ read-only root filesystem
  │    ├─ private PID/filesystem view
  │    ├─ no source/repository mount
  │    ├─ bounded tmpfs profile
  │    └─ no external network
  ├─ starts agent/render job
  │    └─ receives only a local browser endpoint and sealed render payload
  └─ terminates and inspects the whole job/sidecar scope
       └─ emits facts consumed by a small closed-evidence adapter
```

Kubernetes native sidecars are one concrete orchestration model: official
documentation says they start before following app containers, remain available
through the app lifetime, and are terminated after the main container, with
`SIGKILL` after the grace period if necessary. See the
[Kubernetes sidecar documentation](https://kubernetes.io/docs/concepts/workloads/pods/sidecar-containers/)
and [Pod termination lifecycle](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/#pod-termination).
The same ownership shape can be implemented by the existing CVM job supervisor
without adopting Kubernetes.

Prefer Playwright's own `launchServer`/`connect` protocol over raw CDP if the
outer image can carry matching Playwright client/server versions. Retain CDP
only if the existing execution environment cannot expose the Playwright
protocol or the fixed renderer depends on CDP-specific behavior.

### Buy: only if provider-free is negotiable

If product policy later permits a browser provider and egress, Browserbase or
Cloudflare Browser Run removes the greatest amount of operational code. This is
the lowest-maintenance solution for general web agents, but it cannot be used as
evidence for today's provider-free formal run.

### Adopt Browserless: only if a browser fleet is actually needed

Choose self-hosted Browserless when there are multiple concurrent browser
clients, queues, reconnects, recordings, health checks, or fleet operations.
Do not add it merely to perform one sealed residual render; a pinned Playwright
sidecar is smaller and easier to attest.

### Build: retain only the repository-specific adapter and proof vocabulary

Keep custom code for the parts no off-the-shelf product owns:

- conversion from approved image/job identity to Browser Authority evidence;
- experiment/session binding;
- verification that the browser receives no source mount or source-visible
  process root;
- projection of outer process/container results into the closed diagnostic
  vocabulary;
- independent terminal proof for the exact job handle.

Do not keep custom code for browser file copying, dependency discovery,
Playwright/browser version selection, general process reaping, or a second
browser-service session manager if the outer runtime can own those directly.

## What must be validated before changing architecture

The recommendation is conditional on four focused experiments:

1. **CVM capability:** determine whether the provider-free job supervisor can
   start a predeclared second container/process scope and report its exact
   terminal state without granting that authority to the nested agent.
2. **Rendering parity:** compare current and sidecar results byte-for-byte for
   PNG bytes, eight-view ordering, profile digest, and public evidence.
3. **Source-Hidden conformance:** enumerate browser-visible process roots and
   prove none contains the deployment source or a materialization alias. This
   should become simpler because the browser container receives no source mount.
4. **Cleanup-evidence mapping:** define which outer-runtime facts establish that
   the browser process tree, container, profile tmpfs, endpoint, and mounts no
   longer exist. Preserve the current closed public schema unless the spec is
   deliberately revised.

If the CVM runtime cannot prelaunch and own a sidecar, then there is no direct
off-the-shelf replacement under the current constraints. In that case, keep the
custom runtime but split it into deep private Modules for frozen image,
execution tree, owned process, and cleanup arbitration. That is a deployment
constraint, not proof that browser lifecycle belongs in one 2,500-line class.

## Bottom line

The user's simplified model is substantially correct: the product needs a
stable browser available to an agent sandbox. The current implementation is
complex because it additionally tries to construct, attest, hide, supervise,
and formally close that browser **from inside a restricted job**.

The market's dominant answer is to move the browser to a separately owned
service, container, sidecar, or microVM and give the agent only a connection.
For the current provider-free contract, the best fit is the same architecture
implemented locally with a pinned, preloaded Playwright sidecar—not a managed
cloud browser and not more lifecycle machinery inside `browser_runtime.py`.

## Repository environment check

Checked read-only on 2026-08-14:

- DevCloud CVM is `x86_64` and runs Docker 27.3.1 with containerd/runc,
  `overlay2`, systemd cgroups, and cgroup v2. No Playwright, Browserless, Chrome,
  or Chromium OCI image was present in the filtered image inventory.
- The local default Colima profile is configured as `x86_64` with the Docker
  runtime, matching CVM's target platform. It is currently `Broken` and stopped,
  so daemon capability and image inventory could not be inspected.

These facts remove the need for a host-process Sidecar fallback: both target
environments use the OCI Adapter. Before the prototype, Colima must be restored
and one exact `linux/amd64` Sidecar Artifact must be built or provisioned into
both environments under a separately authorized provisioning step.
