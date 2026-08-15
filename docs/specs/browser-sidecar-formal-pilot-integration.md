# Browser Sidecar formal-pilot integration

Status: implementation spec

Fixed base: `90bc24cf8860125b158c5f04ddc5dfd65efbcb39`

## Public seams

1. `meshshot.render_residual_preview(...)` keeps its existing signature,
   `RenderedPreview` result, canonical PNG post-processing, profile identity,
   view order, and error type. The only formal selector is the immutable fixed
   mount `/run/meshshot-browser/authority.json`, and the only connection is
   `/run/meshshot-browser/browser.sock`; callers and environment variables
   cannot select either path. Outside a formal pilot job, absence of the fixed
   mount selects the existing local renderer. A present but malformed,
   replaceable, or unavailable fixed authority fails closed and never falls
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
  `sha256:f0e94aa0fb73a83fb5be7c8f08460b189cbae3118ef97642a44e7fda7fd80980`
- Broker OCI revision / production implementation commit:
  `fdbea34c0b7b80168936e376df1b72f07fc7309f`
- Image source revision:
  `1abe4c97929906b5c0b28b0f3f38857bd923952f`
- Residual program SHA-256:
  `d2138ad7f3b74094862cfa8bd4d3ee0fb59ba8bde89a82962afae9ae02b0180b`
- Viewer program SHA-256:
  `e2e1bfd1a28c4ef7ce312f477a301f8ef5386ecbcb64eb5d586b29bcdbb4728b`

The corrected Broker was built cleanly from the GREEN implementation with
`--pull=false --no-cache --network=none`; its exact identity remains subject
to independent review. Runtime image pulls and browser downloads are
forbidden. Replacing any accepted identity is a new review decision.

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
| Caller changes or unsets an authority/socket environment variable, or supplies a conforming temporary authority/socket | It cannot select formal mode, redirect the fixed connection, or trigger a formal-to-legacy fallback | The fixed mount remains the only formal selector |
| Authority malformed/unreadable/unreachable after formal selection | `MeshshotError`; no local browser launch or transport fallback | Outer runner still performs terminal Sidecar cleanup |
| Render request has extra/missing key, wrong program, URL, JS, path, endpoint, browser/Docker arg, invalid geometry/options, or oversized body | Registered-program broker rejects it before browser work | Fresh context is absent or closed; Sidecar remains job-owned |
| Nested Agent attempts browser spawn or inventory | Formal workload exposes no browser executable/cache and no raw Sidecar endpoint; attempt fails | Nested browser-process inventory remains zero |
| Browser can see source alias or external egress | Formal render and pilot fail closed | Sidecar is terminally stopped and absent |
| Workload exits nonzero | Original workload status is preserved unless lifecycle/cleanup evidence is worse | Sidecar cleanup always runs after process-group terminal state |
| Supervisor receives SIGINT or SIGTERM at any startup or workload boundary | The relay is installed before Sidecar/Broker mutation; signal status wins and no later startup boundary is entered | Every already-created exact resource follows the same bounded cleanup and exact absence proof |
| Sidecar exits during workload | Workload group is terminated; pilot fails closed; no replacement Sidecar starts | Exact terminal state is recorded and resource is absent |
| Broker exits during workload | Workload group is terminated; pilot fails closed; no replacement Broker or host process starts | Exact terminal state is recorded and resource is absent |
| Broker or Sidecar stop/terminal evidence times out | Cleanup failure dominates workload/render success | Remaining exact owned resources still receive their bounded cleanup attempts; retained proof is closed |
| Stop/remove/absence proof fails or an owned resource is retained | Cleanup failure dominates any render/workload success | Receipt names the closed predicate; no stronger cleanup or unrelated deletion |
| Success | One exact Sidecar and one exact browser-less Broker served the whole job; residual parity and Viewer/eight-view predicates pass; each render had a fresh context/page; nested browser inventory/process count is zero | Broker/Sidecar/network exact IDs are terminal and removed, label inventory is empty, source/egress predicates are closed, and no browser process remains in the nested workload |

## Evidence and interruption rules

The outer lifecycle writes one proof-only job receipt under `run/` using
atomic publication. Its exact public keys are the receipt schema/status,
immutable Sidecar/Broker/base/source and program identities, the fixed
predicate map, exact aggregate/program counts, one closed failure marker, and
`retryAllowed:false`. It never publishes Docker State, PIDs, timestamps,
errors, paths, argv/stderr, owner nonces, or resource/job identifiers. A
receipt is successful only when every whitelisted predicate is positive,
both registered programs have at least one accepted request, accepted counts
sum exactly, and `freshContexts = acceptedRequests + 1`.

The fixed authority is opened with no-follow semantics and must be one regular
inode owned by the workload UID, mode `0444`, with link count one and the exact
authority/job/image/program schema. The runner publishes it only after exact
Sidecar, Broker, socket, readiness, and isolation checks, then mounts the
capability directory read-only into bwrap. Nested code cannot remove or replace
the fixed authority/socket mount.

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
