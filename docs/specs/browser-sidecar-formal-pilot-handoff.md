# Browser Sidecar formal-pilot integration handoff

Outcome: **CHANGE_REQUEST**

Ticket: `browser-sidecar-formal-pilot-integration`

Group: `WEG-1` (singleton; serialized runner/render contracts)

Owner: `/root/browser_sidecar_formal_pilot_owner`

## Git identity

- Fixed base: `90bc24cf8860125b158c5f04ddc5dfd65efbcb39`
- Branch: `codex/browser-sidecar-formal-pilot-20260815`
- Worktree:
  `/private/tmp/text-to-cad-browser-sidecar-formal-pilot-20260815`
- Review range:
  `90bc24cf8860125b158c5f04ddc5dfd65efbcb39..HEAD`
- Owner has not self-approved Standards or Spec.

## Implemented checkpoint

- The unchanged public `meshshot.render_residual_preview(...)` seam selects a
  fixed formal Unix-socket authority only when the outer job declares one.
  Formal selection is fail-closed and never launches a local fallback browser.
- `scripts/pilot/runner.py` owns one job lifecycle around the nested workload,
  removes browser caches/executables from bwrap, and exposes only the read-only
  authority/socket directory at `/run/meshshot-browser`.
- `BrowserSidecarJob` attests the exact Sidecar and Broker image IDs, platform,
  OCI revisions, and Broker base identity before resource creation. It rejects
  foreign network/Sidecar/Broker names and uses no pull, host port, host broker,
  egress-capable network, source mount, or arbitrary runtime input.
- The exact browser-less Broker runs beside the Sidecar on one internal Docker
  network, holds the single stable `playwright.connect()` session, and creates
  the job-private Unix socket. The nested workload cannot reach the raw
  Playwright endpoint.
- Registered programs are exactly `residual` and `viewer`. Requests use exact
  key sets and fixed profile, options, URLs, scripts, baked Viewer fixture, and
  projection operation. Every accepted request owns a fresh context/page.
- Readiness and terminal receipts bind isolation preflight, request/program
  counts, `freshContexts = acceptedRequests + 1`, exact Sidecar and Broker
  terminal state, the outer owned-resource ledger, cleanup errors, label
  absence, and `retryAllowed:false`.
- The Broker artifact seals the current public meshshot package plus one fixed
  conformance client. That client can run networkless with only the read-only
  capability bind and prove public residual parity, Viewer transition, nested
  browser inventory/process zero, source-alias absence, and blocked egress.
- The successor adversarial matrix covers missing/wrong Broker identity,
  platform/revision/base mismatch, foreign Broker name, malformed/late/wrong
  readiness, pre-existing/non-socket/replaced socket, premature exits, exact
  request strictness, interruption, cleanup timeout/retention, and success.

## Exact artifact identities

- Sidecar:
  `sha256:22ff2413ffd9dcdb5f62e5dbb2c6e46d6b4e98f0e45dc4698f80eb8f06b146f1`
- Sidecar source revision:
  `1abe4c97929906b5c0b28b0f3f38857bd923952f`
- Browser-less Broker base:
  `sha256:a2dae48401a6918a15e68a97c4c0290ba6a58ec47a3448498aec12885be46373`
- Final corrected Broker:
  `sha256:ac7a7adc13664de5661c674b7c55349b58170e48a0176e02e8bf1586fcc5a036`
- Broker OCI revision:
  `8188bbdc61e31805c7c7c0bae2f8dfb38590dc2c`
- Platform: `linux/amd64`

The final Broker was built cleanly with `--pull=false --no-cache` from the
exact sealed-client digest. Read-only inspection confirmed image ID, platform,
revision, base label, fixed entrypoint, and browser-less user.

## Changed files

- `docs/specs/browser-sidecar-formal-pilot-integration.md`
- `docs/specs/browser-sidecar-formal-pilot-handoff.md`
- `packages/meshshot/browser_sidecar_broker/Dockerfile`
- `packages/meshshot/browser_sidecar_broker/image-lock.json`
- `packages/meshshot/src/meshshot/renderer.py`
- `scripts/pilot/browser_sidecar.py`
- `scripts/pilot/browser_sidecar_conformance.py`
- `scripts/pilot/runner.py`
- `tests/python/global/test_browser_sidecar.py`
- `tests/python/global/test_pilot_runner.py`
- `tests/python/packages/meshshot/test_renderer.py`

The throwaway prototype remains evidence only; production code was not copied
wholesale from it.

## Deterministic verification

Passed before the single conformance attempt:

- Focused Browser Sidecar, runner, and public renderer suites: **56 tests, OK**.
- Final global policy gate after sealing the conformance client:
  **209 tests, OK**.
- Affected meshshot profile/renderer: **8 tests, OK**.
- Public `mesh-compare` preview CLI: **8 tests, OK**.
- `npm --prefix packages/meshshot test`.
- `scripts/dev/setup-symlinks.sh --check`.
- `python -m py_compile` for changed Python and focused tests.
- Full-range `git diff --check`.

The localized post-attempt canonical-bind regression is separately RED/GREEN:
`1fc22bcf` / `8188bbdc`; the focused Browser Sidecar suite is **10 tests, OK**.

Not passed / not complete:

- The earlier full Python wrapper reached unrelated `meshscope` tests but the
  lightweight worktree lacks the native octree backend: 26 errors, one skip.
- Bundle freshness remains **NOT VERIFIED**: the check attempted to fetch the
  missing `esbuild` package and failed DNS. No dependency was installed.
- Independent Standards and Spec/security reviews are **NOT RUN**.

## Single production-shaped conformance attempt

Dedicated Colima profile: `browser-sidecar-prototype`, `linux/amd64`, Docker
socket
`unix:///Users/zhiyuanma/.colima/browser-sidecar-prototype/docker.sock`.

Exactly one successor conformance job was run, with no pull, download, source
mount, external provider, or CVM operation. It used Broker image
`sha256:10a6fe5d644078205e42ec09bb3ef5cb33e5fd390b87ebc4b7870db87b3302ee`
at revision `6ad92fc172ba654ff13b470eb3cbda746eeb0bbe`.

The job passed both image attestations, foreign-name checks, internal network
creation, exact Sidecar start, and exact Sidecar readiness. It then failed
closed before Broker creation with `errorCheck:docker-run-status`. Public
residual, Viewer, and nested-client predicates therefore did not run.

The receipt proves exact cleanup: Sidecar `closing/SIGTERM`, Sidecar exit 0,
empty container/network label inventory, `absenceProof.proved:true`, and no
cleanup errors. The conformance artifact is preserved at:

`/tmp/browser-sidecar-formal-pilot-conformance-20260815.json`

The observed command used a macOS temp path through `/var/...` as a Docker bind
source. Colima exposes the canonical `/private/var/...` path. The localized
fix now resolves the capability directory before constructing the bind source,
has a focused public-boundary regression test, and is present in the final
corrected Broker artifact above. Per the one-attempt rule, it was not rerun.

Earlier pre-Broker design attempts and their disproven host-port hypothesis
remain documented in Git history; they are not counted as successor Broker
conformance executions.

## Resource release state

- Exact successor conformance job containers/networks: absent by receipt and
  exact owner-label inventory.
- Interrupted build container `16fa5b53...`: exact-owned and removed.
- Docker build intermediate containers: removed by successful builds.
- Dedicated Colima profile: left running because it pre-existed this ticket.
- Reviewed and newly built images: retained; no image deletion was authorized.
- No unrelated Docker resource was adopted, stopped, relabeled, or removed.

## Deferred interaction and required next action

Return for independent review of
`90bc24cf8860125b158c5f04ddc5dfd65efbcb39..HEAD`. If review accepts the
localized canonical-bind fix, authorize one new clean-SHA local conformance
attempt using final Broker image
`sha256:ac7a7adc13664de5661c674b7c55349b58170e48a0176e02e8bf1586fcc5a036`.
Do not reuse the failed job handle.

Before paid CVM closure, the provisioning receipt must represent the Broker
without ambiguity. The current fixed role is named `client` and its durable
semantics describe the sealed Agent client, not the Render Program Broker.
No CVM provisioning change or run was authorized here; independent review
should decide whether to rename/extend that exact role or add a distinct Broker
role. Do not weaken provenance by silently recording the Broker as an Agent
client.

No CVM/Venus/provider/model request, push, merge, tracker mutation, dependency
installation, retained-handle access, unrelated cleanup, or image deletion was
performed.
