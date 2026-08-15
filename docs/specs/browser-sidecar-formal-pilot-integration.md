# Browser Sidecar formal-pilot integration

Status: implementation spec

Fixed base: `90bc24cf8860125b158c5f04ddc5dfd65efbcb39`

## Public seams

1. `meshshot.render_residual_preview(...)` keeps its existing signature,
   `RenderedPreview` result, canonical PNG post-processing, profile identity,
   view order, and error type. Outside a formal pilot job, absence of Browser
   Authority selects the existing local renderer. Once an outer authority is
   declared, malformed or unavailable authority fails closed and never falls
   back to a locally launched browser.
2. `scripts/pilot/runner.py run ...` owns exactly one Browser Sidecar for the
   complete nested Agent workload. It starts the Sidecar before bwrap, gives
   bwrap only a job-private registered-program connection, and releases every
   owned resource after the workload process group is terminal.
3. The only first-release Render Programs are `residual` and `viewer`. Their
   requests have exact schemas and fixed URLs, scripts, image identity,
   browser options, profile, camera policy, and Viewer inspection operation.
   Callers cannot supply arbitrary URL, JavaScript, executable, path,
   environment, endpoint, browser argument, Docker argument, or image value.

## Fixed artifact identity

- Platform: `linux/amd64`
- Sidecar image:
  `sha256:22ff2413ffd9dcdb5f62e5dbb2c6e46d6b4e98f0e45dc4698f80eb8f06b146f1`
- Browser-less Broker base image:
  `sha256:a2dae48401a6918a15e68a97c4c0290ba6a58ec47a3448498aec12885be46373`
- Render Program Broker image:
  `sha256:fe610b09016459c32fbb8159ffd43c3a8ba232cdd0014149de917909c9d1ff47`
- Broker OCI revision / production implementation commit:
  `f782f0519d39ae18047891419947c0332b6e637a`
- Image source revision:
  `1abe4c97929906b5c0b28b0f3f38857bd923952f`
- Residual program SHA-256:
  `d2138ad7f3b74094862cfa8bd4d3ee0fb59ba8bde89a82962afae9ae02b0180b`
- Viewer program SHA-256:
  `e2e1bfd1a28c4ef7ce312f477a301f8ef5386ecbcb64eb5d586b29bcdbb4728b`

These are the exact reviewed R8 artifacts. Runtime image pulls and browser
downloads are forbidden. Replacing any identity is a new review decision.

## Adversarial matrix

| Case | Required public observation | Cleanup obligation |
| --- | --- | --- |
| Sidecar image missing | Pilot fails before nested workload starts; no pull, build, retry, or legacy browser fallback | No owned Docker resource remains |
| Image ID, platform, or source revision wrong | Pilot fails before create; the exact reviewed ID is not weakened to a tag or prefix | No owned Docker resource remains |
| Broker image missing | Pilot fails before any job resource is created; no pull, build, retry, host broker, or legacy fallback | No owned Docker resource remains |
| Broker image ID, platform, base identity, or OCI revision wrong | Pilot fails before create; both image ID and the production implementation revision are exact receipt identities | No owned Docker resource remains |
| Foreign predictable container/network name exists | Pilot fails; it does not adopt, stop, remove, or relabel the foreign resource | Foreign resource is untouched; owned-label absence is proved |
| Foreign predictable Broker container name exists | Pilot fails before network creation; it does not adopt, stop, remove, or relabel the foreign resource | Foreign Broker is untouched; owned-label absence is proved |
| Readiness absent, malformed, late, or for another job | Nested workload never starts; pilot is terminal failed | Exact owned Sidecar/network are stopped and absent |
| Broker readiness absent, malformed, late, or for another job | Authority is never published and nested workload never starts | Exact owned Broker, Sidecar, and network are stopped and absent |
| Broker socket path pre-exists, is not one socket, changes identity, or remains writable to the nested workload | Pilot fails before authority publication | Exact owned Broker, Sidecar, and network are stopped and absent; no foreign path is removed |
| Authority absent outside formal job | Existing local `render_residual_preview` behavior remains selected | Existing local browser cleanup remains unchanged |
| Authority malformed/unreadable/unreachable after formal selection | `MeshshotError`; no local browser launch or transport fallback | Outer runner still performs terminal Sidecar cleanup |
| Render request has extra/missing key, wrong program, URL, JS, path, endpoint, browser/Docker arg, invalid geometry/options, or oversized body | Registered-program broker rejects it before browser work | Fresh context is absent or closed; Sidecar remains job-owned |
| Nested Agent attempts browser spawn or inventory | Formal workload exposes no browser executable/cache and no raw Sidecar endpoint; attempt fails | Nested browser-process inventory remains zero |
| Browser can see source alias or external egress | Formal render and pilot fail closed | Sidecar is terminally stopped and absent |
| Workload exits nonzero | Original workload status is preserved unless lifecycle/cleanup evidence is worse | Sidecar cleanup always runs after process-group terminal state |
| Supervisor receives SIGINT or SIGTERM | Signal is relayed to the workload group; signal status wins | Sidecar receives bounded termination and is absent before return |
| Sidecar exits during workload | Workload group is terminated; pilot fails closed; no replacement Sidecar starts | Exact terminal state is recorded and resource is absent |
| Broker exits during workload | Workload group is terminated; pilot fails closed; no replacement Broker or host process starts | Exact terminal state is recorded and resource is absent |
| Broker or Sidecar stop/terminal evidence times out | Cleanup failure dominates workload/render success | Remaining exact owned resources still receive their bounded cleanup attempts; retained proof is closed |
| Stop/remove/absence proof fails or an owned resource is retained | Cleanup failure dominates any render/workload success | Receipt names the closed predicate; no stronger cleanup or unrelated deletion |
| Success | One exact Sidecar and one exact browser-less Broker served the whole job; residual parity and Viewer/eight-view predicates pass; each render had a fresh context/page; nested browser inventory/process count is zero | Broker/Sidecar/network exact IDs are terminal and removed, label inventory is empty, source/egress predicates are closed, and no browser process remains in the nested workload |

## Evidence and interruption rules

The outer lifecycle writes one job receipt under `run/` using atomic
publication. It binds the job, exact image and program identities, readiness,
request count, fresh-context observations, workload terminal status, Sidecar
terminal state, owned-resource ledger, absence proof, and first closed failure
classification. A receipt is successful only when every predicate is positive.

SIGINT/SIGTERM, workload failure, readiness failure, Sidecar exit, malformed
broker traffic, and cleanup failure all traverse the same terminal cleanup
path. There is one attempt per job: no resource adoption, Sidecar replacement,
retry, pull, download, source mount, egress fallback, host-process browser, or
legacy browser fallback after formal authority selection.

## Validation boundary

Deterministic tests use the public render API and pilot job boundary while
faking only operating-system/Docker/Playwright boundaries. The local
production-shaped gate uses the dedicated reviewed Colima profile, both exact
reviewed images where required, `--pull=never`, and no external provider. CVM,
Venus, push, merge, tracker mutation, retained-handle access, and image cleanup
remain not authorized.
