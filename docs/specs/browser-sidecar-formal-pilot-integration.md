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
  `sha256:45f0655ccb362f01097876d9e6d905139cb48058adcb51621472362cadc593dd`
- Broker OCI revision / production implementation commit:
  `d2ee3b689b471e29b1b114a9400144867ff06531`
- Image source revision:
  `1abe4c97929906b5c0b28b0f3f38857bd923952f`
- Residual program SHA-256:
  `d2138ad7f3b74094862cfa8bd4d3ee0fb59ba8bde89a82962afae9ae02b0180b`
- Viewer program SHA-256:
  `e2e1bfd1a28c4ef7ce312f477a301f8ef5386ecbcb64eb5d586b29bcdbb4728b`

The corrected Broker was built cleanly from that exact full GREEN commit with
`--pull=false --no-cache --network=none`. Its Dockerfile-copied files were
extracted from one non-running inspection container and proved byte-identical
to the commit; the container was removed. Its exact identity remains subject
to independent review. Runtime image pulls and browser downloads are forbidden.
Replacing any accepted identity is a new review decision.

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
| Nested-gate proof is absent, malformed, duplicated, late, for a different job/nonce, or has the wrong artifact/surface identity | The outer-owned channel rejects it and never releases the gate to exec the Agent workload; a Job A proof cannot satisfy Job B | Exact owned Broker, Sidecar, and network follow terminal cleanup; the channel is closed and its socket is absent |
| Authority absent outside formal job | Existing local `render_residual_preview` behavior remains selected | Existing local browser cleanup remains unchanged |
| Caller changes or unsets an authority/socket environment variable, or supplies a conforming temporary authority/socket | It cannot select formal mode, redirect the fixed connection, or trigger a formal-to-legacy fallback | The fixed mount remains the only formal selector |
| Authority malformed/unreadable/unreachable after formal selection | `MeshshotError`; no local browser launch or transport fallback | Outer runner still performs terminal Sidecar cleanup |
| Render request has extra/missing key, wrong program, URL, JS, path, endpoint, browser/Docker arg, invalid geometry/options, or oversized body | Registered-program broker rejects it before browser work | Fresh context is absent or closed; Sidecar remains job-owned |
| Mounted Agent surface contains a Chromium/Chrome/Playwright package, executable, cache, ELF, or product marker under a renamed/distro path | Before Sidecar startup, the runner scans every exact read-only mount plus writable experiment/Codex state, fails on an uninspectable or writable finding, and reduces read-only findings to one deterministic shortest-directory mask antichain | The sealed gate accepts only the same canonical manifest, rechecks each non-overlapping empty mask, and proves zero Chromium processes in the future Agent namespace before exec |
| Exact mounted root vanishes, an entry cannot be lstat/open/read/scandir inspected, or a link is dangling, escaping, cyclic, or uninspectable | The shared descriptor walker never follows a link implicitly; required roots and every entry fail closed while an explicitly optional absent non-mounted root alone is ignored | No Sidecar starts, or the already-owned job enters terminal cleanup; the Agent is never released on a surface-proof mismatch |
| Nested Agent attempts browser spawn or inventory | The fixed preflight proves the closed mounted surface has no browser package/executable/cache and zero visible Chromium processes; the later Agent receives no browser lifecycle authority or raw Sidecar endpoint | Nested browser-process inventory remains zero |
| Sidecar Browser Execution Tree can see a source alias or external egress | The Docker-internal Sidecar/Broker preflight fails closed; this predicate is never inferred from Agent paths or an Agent HTTP request | Sidecar is terminally stopped and absent |
| Workload exits nonzero | Original workload status is preserved unless lifecycle/cleanup evidence is worse | Sidecar cleanup always runs after process-group terminal state |
| Supervisor receives SIGINT or SIGTERM at any startup or workload boundary | The relay is installed before Sidecar/Broker mutation; signal status wins and no later startup boundary is entered | Every already-created exact resource follows the same bounded cleanup and exact absence proof |
| Sidecar exits during workload | Workload group is terminated; pilot fails closed; no replacement Sidecar starts | Exact terminal state is recorded and resource is absent |
| Broker exits during workload | Workload group is terminated; pilot fails closed; no replacement Broker or host process starts | Exact terminal state is recorded and resource is absent |
| Broker or Sidecar stop/terminal evidence times out | Cleanup failure dominates workload/render success | Remaining exact owned resources still receive their bounded cleanup attempts; retained proof is closed |
| Stop/remove/absence proof fails or an owned resource is retained | Cleanup failure dominates any render/workload success | Receipt names the closed predicate; no stronger cleanup or unrelated deletion |
| Success | One exact Sidecar and one exact browser-less Broker served the whole job; Sidecar preflight directly proves Source-Hidden/no external route, the Broker proves raw registered-program/fresh-context facts, and the nested gate separately proves public residual parity, Viewer transition, and Agent browser inventory/process zero before it execs the workload exactly once | Broker/Sidecar/network exact IDs are terminal and removed, label inventory is empty, Sidecar source/egress predicates are closed, and no browser process is visible at Agent preflight |

## Evidence and interruption rules

The outer lifecycle writes one proof-only job receipt under `run/` using
atomic publication. Its exact public keys are the receipt schema/status,
immutable Sidecar/Broker/base/source and program identities, the fixed
predicate map, exact aggregate/program counts, one closed failure marker, and
`retryAllowed:false`. It never publishes Docker State, PIDs, timestamps,
errors, paths, argv/stderr, owner nonces, or resource/job identifiers. A
receipt is successful only when every whitelisted predicate is positive,
both registered programs have at least one accepted request, accepted counts
sum exactly, and `freshContexts = acceptedRequests + 1`. Broker predicates
cover only the raw registered programs and lifecycle evidence it observes;
`residualPublicParity` is not a Broker claim. `sidecarSourceHidden` and
`sidecarEgressBlocked` come only from the Docker-internal Browser/Sidecar
preflight. The separate `nestedGate` predicate group covers only the unchanged
public API, Viewer, and Agent browser inventory/process checks. It makes no
claim about Agent source visibility or Agent egress.

The fixed authority is opened with no-follow semantics and must be one regular
inode owned by the workload UID, mode `0444`, with link count one and the exact
authority/job/nonce/image/program schema. The runner publishes it only after
exact Sidecar, Broker, socket, readiness, and isolation checks, then mounts the
capability directory read-only into bwrap. Nested code cannot remove or
replace the fixed authority/socket mount.

Before Sidecar startup, the outer runner deterministically packages the fixed
gate plus exact meshshot runtime into one digest-bound zipapp. Its shared
surface scanner uses no-follow directory and file descriptors, verifies each
opened inode against its lstat identity, propagates all inspection errors, and
resolves every link explicitly without leaving its declared root. Required
exact mounts cannot disappear; only an explicitly optional root that was never
mounted may be absent. Dangling, escaping, cyclic, or uninspectable links close
the job, while a reachable in-root target is inspected once and contributes a
canonical deterministic mask. The scanner detects named or renamed Chromium/
Chrome/Playwright packages, executables, caches, ELF and product markers;
writable experiment/Codex state must already be empty of all such findings.
Exact duplicate masks choose one stable identity, and the shortest selected
directory `tmpfs` mask removes every descendant directory/file mask. The
resulting deterministic antichain is the only exclusion set accepted by the
outer bwrap builder or nested gate, avoiding mutually exclusive parent-empty
and child-exists predicates. The fixed authority and
read-only gate input bind the same job ID, fresh nonce, artifact digest, and
exact mounted-surface manifest digest. There is no live meshshot source bind.

Immediately before Agent execution, bwrap starts that zipapp in the exact PID,
filesystem, and network namespace that will contain the Agent. It rechecks the
manifest and empty masks, calls the unchanged public residual API with a
literal fixture, makes the fixed registered Viewer request, and checks exact
PNG hash/mode/size/profile/views, Viewer projection/no-artifact state, browser
package/executable/cache absence, and zero Chromium processes. It writes one
exact-key identity-bound proof to an outer-owned one-shot Unix socket. The outer
runner accepts only the first proof, unlinks the listener, validates the proof,
closes the channel, and only then releases the gate to `execvpe` the already
fixed workload argv without shell interpolation. Missing, malformed,
duplicate, or late proof therefore cannot be replaced by a later Agent ACK.

The production-shaped local host uses the same sealed runner composition. A
fixed network-none discovery role runs in the exact digest-pinned browser-less
client image before Sidecar startup and inventories its immutable execution
surface. The host accepts only its exact schema and fixed root vocabulary,
canonicalizes the discovered masks through the shared runner operation, and
binds the resulting manifest, gate artifact, job ID, and fresh nonce before
`BrowserSidecarJob.start`. The same isolation and masks are then applied while
the one-shot Browser Gate proves public residual parity, Viewer transition, and
nested browser inventory/process zero before releasing the fixed
Agent-equivalent client exactly once. Docker and spawned-process boundaries are
the only fakes in the host regression.

Source-Hidden and blocked external browser egress remain direct Sidecar facts:
the Browser and Broker use one Docker `--internal` network, no source mount,
baked fixed assets, and a browser-context preflight. Agent repository/skill
visibility is required by the pilot and is not evidence about the Browser
Execution Tree. The historical standalone conformance client is not copied
into the production Broker artifact; acceptance uses the sealed runner gate,
so the two principals cannot be conflated by duplicated production checks.

SIGINT/SIGTERM, workload failure, readiness failure, Sidecar exit, malformed
broker traffic, and cleanup failure all traverse the same terminal cleanup
path. There is one attempt per job: no resource adoption, Sidecar replacement,
retry, pull, download, source mount, egress fallback, host-process browser, or
legacy browser fallback after formal authority selection.

A failure before Docker resolution still enters terminal publication. Because
the fresh owner has not created a network or container at that point, its
receipt records exact absence true and retains the original closed startup
check rather than manufacturing an `absence-proof` cleanup failure.

## Validation boundary

Deterministic tests use the public render API and pilot job boundary while
faking only operating-system/Docker/Playwright boundaries. The local
production-shaped gate uses the dedicated reviewed Colima profile, both exact
reviewed images where required, `--pull=never`, and no external provider. CVM,
Venus, push, merge, tracker mutation, retained-handle access, and image cleanup
remain not authorized.
