# Browser Sidecar formal-pilot integration handoff

Outcome: **CHANGE_REQUEST**

Ticket: `browser-sidecar-formal-pilot-integration`

Group: `WEG-1` (singleton; serialized runner/render contracts)

Owner: `/root/browser_sidecar_formal_pilot_owner`

## Git identity

- Fixed base: `90bc24cf8860125b158c5f04ddc5dfd65efbcb39`
- Implementation checkpoint before this docs-only handoff: `99cb671a`
- Branch: `codex/browser-sidecar-formal-pilot-20260815`
- Worktree: `/private/tmp/text-to-cad-browser-sidecar-formal-pilot-20260815`
- Review range: `90bc24cf8860125b158c5f04ddc5dfd65efbcb39..HEAD`
- Owner has not self-approved Standards or Spec.

## Implemented checkpoint

The checkpoint implements the deterministic production seams but is not ready
to land because the local production-shaped Docker gate found a new artifact
boundary.

- `meshshot.render_residual_preview(...)` keeps its signature and
  `RenderedPreview` projection. Absence of formal Browser Authority selects the
  existing local path. Presence selects one exact-schema Unix-socket residual
  Render Program; malformed authority/request/response fails closed with no
  local browser fallback.
- Formal authority validates an exact regular, single-link,
  non-group/world-writable inode. Runner creates authority/socket in a private
  temporary directory and read-only binds it at fixed
  `/run/meshshot-browser`; it is not under the Agent-writable experiment tree.
- `scripts/pilot/runner.py` constructs one job Sidecar before the nested
  workload, injects only the fixed authority file, removes the Playwright
  browser cache from bwrap, monitors premature Sidecar/broker exit, and closes
  the Sidecar after workload-process-group terminal state.
- `BrowserSidecarJob` requires the exact reviewed `linux/amd64` image ID and
  source revision, rejects foreign names, uses `--pull=never`, an internal
  Docker network, read-only root, dropped capabilities, no-new-privileges,
  bounded resources/tmpfs, no source mount, and no arbitrary image/Docker
  input.
- The broker holds one stable `playwright.connect()` connection. Its
  `residual` and `viewer` programs accept exact-key structured input only and
  create/close one fresh context/page per accepted request.
- Residual preserves profile/camera/options/eight-view order and public Python
  PNG post-processing. Viewer accepts only the baked inspection STEP and the
  real projection toggle, using the proven focus/keyboard/ARIA transition.
- Broker preflight requires the exact baked Sidecar authority, Playwright
  1.60.0, Chromium revision 1223/version 148.0.7778.96, registered program
  digests, no visible source alias, and blocked browser egress.
- Terminal receipt binds readiness/isolation, per-program accepted request
  counts, `freshContexts = acceptedRequests + preflight`, workload status,
  exact `closing/SIGTERM`, Sidecar exit 0, reverse-order cleanup, exact-label
  absence, and `retryAllowed:false`. Cleanup timeout/failure is closed and
  continues only safe exact-ID removal plus absence proof.

The committed RED/GREEN history is intentionally granular. The primary pairs
cover public authority transport, outer lifecycle, residual broker, runner
ownership, Viewer, immutable capability, authority inode, isolation preflight,
terminal broker accounting, cleanup timeout, and exact closing signal.

## Changed files

- `docs/specs/browser-sidecar-formal-pilot-integration.md`
- `docs/specs/browser-sidecar-formal-pilot-handoff.md`
- `packages/meshshot/src/meshshot/renderer.py`
- `scripts/pilot/browser_sidecar.py`
- `scripts/pilot/runner.py`
- `tests/python/packages/meshshot/test_renderer.py`
- `tests/python/global/test_browser_sidecar.py`
- `tests/python/global/test_pilot_runner.py`

No prototype production file was copied wholesale. The fixed request schema,
program interactions, identities, and proven Viewer control were extracted;
`packages/meshshot/prototypes/browser_sidecar/` remains throwaway evidence.

## Verification

Passed:

- Focused formal render/lifecycle/broker/runner suites.
- Global policy gate with the project virtualenv and loopback permission:
  **204 tests, OK**.
- Affected meshshot profile/renderer: **8 tests, OK** with local Chromium
  process access.
- Public `mesh-compare` preview CLI: **8 tests, OK** with local Chromium
  process access.
- `python -m py_compile` for all changed Python and focused test files.
- `npm --prefix packages/meshshot test`.
- `scripts/dev/setup-symlinks.sh --check`.
- `git diff --check` throughout RED/GREEN commits.

Not passed / not complete:

- The repository-wide Python wrapper reached unrelated `meshscope` tests but
  the lightweight worktree has no native octree backend: **26 errors, one
  skip**. No changed Browser Sidecar seam depends on that native extension.
- `scripts/bundle/bundle.sh --check` reached bundle-capable outputs, then tried
  to fetch missing `esbuild` and failed DNS (`registry.npmjs.org`). Dependency
  install/download was not authorized, so bundle freshness is **NOT VERIFIED**.
- Independent Standards review: **NOT RUN**.
- Independent Spec/security review: **NOT RUN**.

## Exact local Docker result

Dedicated profile:
`browser-sidecar-prototype`, `linux/amd64`, Docker socket
`unix:///Users/zhiyuanma/.colima/browser-sidecar-prototype/docker.sock`.

Read-only identity inspection passed before execution:

- Sidecar:
  `sha256:22ff2413ffd9dcdb5f62e5dbb2c6e46d6b4e98f0e45dc4698f80eb8f06b146f1`
- Sealed browser-less client:
  `sha256:a2dae48401a6918a15e68a97c4c0290ba6a58ec47a3448498aec12885be46373`
- Both: `linux/amd64`, source revision
  `1abe4c97929906b5c0b28b0f3f38857bd923952f`.

Two local attempts used no pull, build, download, provider, source mount, or
external network. Both reached exact Sidecar image validation, internal
network/container creation, and exact Sidecar readiness, then failed before
broker startup at the fixed host port projection:

`docker port <exact-owned-container-id> 3000/tcp`

The first used standard ephemeral bind `127.0.0.1::3000`. A localized
RED/GREEN tried explicit `127.0.0.1:0:3000`; the second attempt failed at the
same predicate, disproving that syntax hypothesis. Commit `99cb671a` removes
that speculative expectation and restores the standard form.

After each attempt, exact job-label queries returned zero containers and zero
networks. No residual public render, Viewer run, sealed-client inventory probe,
visual preview, or conformance evidence artifact was produced. The dedicated
Colima profile remains running because its start command reported it was
already running; it was not treated as an owned per-job resource.

## Required decision

The current host-broker design needs a host-accessible raw Playwright endpoint,
but the accepted internal/no-egress/no-host-port Sidecar topology does not
provide one in the reviewed Colima production adapter. Do not attach the
Sidecar to an egress-capable network, expose an unauthenticated raw browser
port, weaken Source-Hidden, or add a legacy browser fallback merely to make the
gate pass.

Recommended decision: introduce a separately digest-pinned, browser-less
**Render Program Broker artifact** on the same internal Docker network. It
would receive only the private Unix-socket directory bind, hold the stable
`playwright.connect()` connection internally, and expose only exact registered
programs to bwrap. This requires accepting and reviewing a new artifact
identity/lifecycle entry (and deciding whether it derives from the exact
reviewed sealed-client image or is a new production image). That artifact was
not pre-agreed, so this owner did not invent it silently.

Rejected shortcut: make the Sidecar network non-internal or publish a raw host
browser endpoint. That diverges from the proven no-egress/no-host-port
topology and expands authority beyond the fixed registered-program socket.

## Not-run authorization boundaries

No CVM operation, Venus/provider/model request, paid work, push, merge, tracker
mutation, dependency installation, runtime image pull/build, retained-handle
access, unrelated cleanup, or image deletion occurred.

## Recommended next action

Resolve the Broker Artifact decision in the authoritative spec/ADR. If the
separate internal broker artifact is accepted, follow up this same owner on the
existing branch: add the new exact identity with RED tests, replace host-port
publication with internal broker-container lifecycle, rerun deterministic
gates, then make one new exact-image local conformance attempt. Only after a
clean successful receipt should independent Standards and Spec review begin.
