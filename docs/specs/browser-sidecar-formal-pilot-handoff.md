# Browser Sidecar formal-pilot integration handoff

Outcome: **READY_FOR_FULL_DUAL_REVIEW** (not accepted or complete)

Ticket: `browser-sidecar-formal-pilot-integration`

Group: `WEG-1` (singleton; serialized runner/render contracts)

Owner: `/root` (continuation of `/root/browser_sidecar_formal_pilot_owner`)

## Git identity

- Fixed base: `90bc24cf8860125b158c5f04ddc5dfd65efbcb39`
- Branch: `codex/browser-sidecar-formal-pilot-resume-20260815`
- Worktree:
  `/private/tmp/text-to-cad-browser-sidecar-formal-pilot-resume-20260815`
- Review range:
  `90bc24cf8860125b158c5f04ddc5dfd65efbcb39..HEAD`
- Owner has not self-approved Standards or Spec.

The original ephemeral worktree was cleared by the host after the prior session,
but its committed HEAD `fd4a9db9...` and branch metadata remained. This
continuation created the isolated worktree above from that exact commit and
reconstructed only the uncommitted image-lock/spec patch recorded verbatim in
the session JSONL. The dirty `develop` root was not used or modified.

## Implemented checkpoint

- The unchanged public `meshshot.render_residual_preview(...)` seam recognizes
  only `/run/meshshot-browser/authority.json` and connects only
  `/run/meshshot-browser/browser.sock`. Caller/environment-selected paths were
  removed. The authority is opened no-follow and must be one exact regular,
  UID-owned, `0444`, link-count-one inode. Absence selects legacy only outside
  the fixed formal mount; any present invalid authority fails without fallback.
- `scripts/pilot/runner.py` owns one job lifecycle around the nested workload,
  installs the signal relay before any Sidecar/Broker lifecycle mutation,
  removes browser caches/executables from bwrap, and exposes only the read-only
  authority/socket directory at `/run/meshshot-browser`.
- Before Sidecar startup, the runner enumerates every exact read-only execution
  mount plus writable experiment/Codex state. It detects named and renamed
  Chromium/Chrome/Playwright packages, executables, caches, ELF and product
  markers through a shared descriptor/no-follow walker. Every required root and
  lstat/open/read/scandir result is closed; only an explicitly optional absent
  non-mounted root is ignored. Every link is resolved explicitly: dangling,
  escaping, cyclic, or uninspectable targets close, and reachable in-root
  targets are inspected once. Read-only findings receive canonical deterministic
  bwrap masks that are rechecked by the same scanner in the nested namespace.
  Exact duplicates are stable, and a shortest covering directory `tmpfs` mask
  removes every descendant mask. The outer bwrap builder and nested gate reject
  any non-antichain manifest, so parent-empty and child-exists predicates cannot
  conflict.
- Immediately before Agent exec, the runner starts the fixed repository-owned
  Browser Gate inside the exact same bwrap PID/filesystem/network environment.
  The gate calls the real unchanged public residual API with one literal
  fixture, calls the fixed registered Viewer projection program, and proves
  exact public PNG/profile/view parity, Viewer transition/no-artifact state,
  browser package/executable/cache and process absence. It does not claim that
  Agent source is hidden or that an Agent HTTP probe proves network policy.
- The gate and exact meshshot runtime are packaged deterministically into one
  immutable digest-bound zipapp mounted read-only at
  `/run/meshshot-browser/browser-gate.pyz`; there is no live development-source
  bind. The fixed authority and gate input share the exact job ID and fresh
  nonce and bind the artifact and mounted-surface manifest digests.
- The local production-shaped host now uses that same runner-owned composition
  instead of starting a job with no gate identity. A fixed sealed discovery
  role inventories the exact immutable browser-less client-image surface under
  the same read-only, network-none, fresh-home isolation used by execution.
  Its canonical exclusions are bound into the job/nonce gate input before
  Sidecar startup. The one-shot gate proof is validated and released before the
  fixed Agent-equivalent client is exec'd; Docker/process calls are the only
  faked boundaries in the public host regression.
- Every host bind source is canonicalized before Docker argv construction,
  including macOS `/var` aliases, discovery artifacts, capability directories,
  and fixed client/gate inputs. Container creation returns one exact immutable
  ID which is immediately checked for the exact job and owner-nonce labels.
  Start, terminal observation, cleanup, and absence use only that ID: predictable
  names are never cleanup authority, and foreign collisions, lost create output,
  or name replacement remain untouched and fail closed.
- The gate publishes one exact-key bounded proof over an outer-owned one-shot
  Unix socket. The outer unlinks/closes the listener after the first connection,
  validates that proof, sends the release byte, and closes the accepted channel
  before the gate uses `execvpe` on the already-fixed workload argv. Missing,
  malformed, duplicate, or late proof prevents Agent exec and enters the same
  terminal cleanup. The later Agent cannot forge a post-hoc ACK.
- Production-validator tests now alter the artifact digest and surface digest
  independently. A run-pilot test carries a wrong surface digest through the
  real `BrowserSidecarJob.record_nested_gate` validator, proves the Agent release
  is withheld, invokes real job close, and observes the closed failed receipt
  and absence predicate.
- Writable experiment/Codex state is inspected by the actual gate-preparation
  path. A writable Playwright cache/executable fails before Sidecar start,
  publishes no sealed gate files, and creates no Sidecar resource.
- `BrowserSidecarJob` attests the exact Sidecar and Broker image IDs, platform,
  OCI revisions, and Broker base identity before resource creation. It rejects
  foreign network/Sidecar/Broker names and uses no pull, host port, host broker,
  egress-capable network, source mount, or arbitrary runtime input.
- The Broker receives one separate writable `broker/` subdirectory only. Gate
  proof, gate input, authority, and lifecycle evidence remain outer-owned
  siblings that are never mounted into the Broker. The public fixed socket is
  an outer-created, identity-checked relative link to `broker/browser.sock`, so
  the read-only Agent mount keeps the unchanged public socket path without
  granting the Broker write authority over the public capability directory.
- Network, Sidecar, Broker, conformance-surface, and conformance-client
  resources use `create`, exact returned-ID label verification, then `start`.
  An unverified ID is never assigned as cleanup authority, started, or removed.
  Conformance cleanup likewise receives only IDs that passed ownership proof.
- The nested process scan recognizes Chromium distro aliases, Chrome variants,
  headless shell, and crashpad names, and fails closed when a live `/proc` entry
  cannot be inspected except for a PID that demonstrably vanished.
- The exact browser-less Broker runs beside the Sidecar on one internal Docker
  network, holds the single stable `playwright.connect()` session, and creates
  the job-private Unix socket. The nested workload cannot reach the raw
  Playwright endpoint.
- Registered programs are exactly `residual` and `viewer`. Requests use exact
  key sets and fixed profile, options, URLs, scripts, baked Viewer fixture, and
  projection operation. Every accepted request owns a fresh context/page.
- Formal success requires accepted residual and Viewer requests, exact program
  totals, `freshContexts = acceptedRequests + 1`, Broker-observed raw residual/
  eight-view and Viewer predicates, separately observed nested public parity/
  Viewer/inventory/process predicates, directly observed Sidecar Source-Hidden/
  egress predicates, zero terminal states,
  exact Sidecar closing, workload success, and absence proof. The Broker no
  longer claims `residualPublicParity`.
- The public receipt is proof-only and exact-keyed. It exposes immutable image,
  source/base, and registered-program identities; fixed closed predicates;
  exact aggregate counts; one closed failure marker; and
  `retryAllowed:false`. It exposes no raw Docker State, PID, timestamp, error
  text, path, argv/stderr, owner/resource/job identifier, or cleanup ledger.
- First cleanup failure is stable; later ordinary failures cannot overwrite it.
  Positive retained-resource proof alone overrides it. Nonzero Broker/Sidecar
  terminal state, closing drift/absence, and terminal timeout are closed
  failures that dominate workload success, and the runner accepts only an
  exact `status:succeeded` receipt with every predicate true.
- A startup failure before Docker resolution now retains its exact closed check
  and truthfully proves absence when no network, Sidecar, or Broker handle could
  have been created. It no longer misclassifies a missing gate identity as an
  `absence-proof` failure.
- `finalize_pilot` preserves signal status 130/143 across missing or invalid
  rollout evidence and artifact/manifest collection failures; postmortem
  preservation still runs, but a later publication error cannot mask SIGINT or
  SIGTERM.
- The Broker artifact seals the fixed registered raw residual and Viewer
  programs. Public residual parity and nested namespace isolation belong only
  to the separate fixed Browser Gate, not to a Broker-reported boolean. The
  fixed browser-less conformance client is sealed at the exact path released by
  that gate. Its host-only runner/contract dependencies are lazy imports and are
  not copied into the Broker.
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
  `sha256:db02c3ff8e5784c253eca924bd48043a4612da52fef0cbc0300f723f18d7e819`
- Broker OCI revision:
  `c197a36047bf628736d639202bfebf612e8bb92d`
- Deterministic sealed Browser Gate zipapp for the current scanner source:
  `sha256:05f4b5b8b99380677c7861e04cb8a9a3c1624206bad59696f825e7f0d4c55c5c`
- Platform: `linux/amd64`

The corrected Broker was rebuilt cleanly with
`--pull=false --no-cache --network=none` from a `git archive` context containing
only the exact committed Dockerfile, Broker/client sources, and meshshot
runtime, based on the exact sealed-client digest. This excluded mutable
working-tree bytecode and reduced the build context to the reviewed paths.
Read-only inspection confirmed image ID, `linux/amd64`, the GREEN source
revision, base label, fixed entrypoint, and browser-less `pwuser`. No
conformance container was launched in this review-correction pass. `git
cat-file` resolves the complete OCI revision as a commit. Extraction from one
non-running, network-none inspection container proved every Dockerfile-copied
source path byte-identical to that exact revision; the inspection container was
then removed.

## Changed files

- `docs/specs/browser-sidecar-formal-pilot-integration.md`
- `docs/specs/browser-sidecar-formal-pilot-handoff.md`
- `packages/meshshot/browser_sidecar_broker/Dockerfile`
- `packages/meshshot/browser_sidecar_broker/Dockerfile.dockerignore`
- `packages/meshshot/browser_sidecar_broker/image-lock.json`
- `packages/meshshot/pyproject.toml`
- `packages/meshshot/src/meshshot/browser_contract.json`
- `packages/meshshot/src/meshshot/renderer.py`
- `scripts/pilot/browser_sidecar.py`
- `scripts/pilot/browser_sidecar_conformance.py`
- `scripts/pilot/browser_gate_contract.py`
- `scripts/pilot/browser_sidecar_gate.py`
- `scripts/pilot/browser_surface.py`
- `scripts/pilot/cvm_sidecar_probe.py`
- `scripts/pilot/runner.py`
- `.claude/skills/cvm-sidecar-probe/SKILL.md`
- `tests/python/global/test_browser_sidecar.py`
- `tests/python/global/test_browser_sidecar_conformance.py`
- `tests/python/global/test_browser_sidecar_broker_image.py`
- `tests/python/global/test_browser_sidecar_gate.py`
- `tests/python/global/test_browser_surface.py`
- `tests/python/global/test_cvm_sidecar_probe.py`
- `tests/python/global/test_pilot_runner.py`
- `tests/python/packages/meshshot/test_renderer.py`
- `tests/python/fixtures/browser_sidecar_image_harness.py`
- `tests/python/fixtures/browser_sidecar_linux_baseline.py`

The throwaway prototype remains evidence only; production code was not copied
wholesale from it.

## Deterministic verification

Passed for the current review corrections:

- Exact locked-image extraction and real packaged-client gate: **2 tests, OK**.
- Focused Browser Sidecar, conformance host, sealed-image contract, surface
  scanner, nested gate, runner, and public renderer suites: **103 tests, OK**
  (**2 opt-in image tests skipped** in this non-Docker aggregate and passed
  separately above).
- Global policy gate: **254 tests, OK**, with the same 2 opt-in image tests
  skipped there and passed separately against the exact locked image.
- CVM Sidecar prepare/provision/probe suite: **48 tests, OK**, including both
  legacy two-role and Formal three-role provisioning receipts.
- Affected meshshot profile/renderer: **11 tests, OK**.
- Public `mesh-compare` preview CLI: **8 tests, OK**.
- `npm --prefix packages/meshshot test`.
- `scripts/dev/setup-symlinks.sh --check`.
- `python -m py_compile` for changed Python and focused tests.
- Full-range `git diff --check`.
- Clean networkless/no-pull Broker build and exact read-only image inspection.

The conformance-host correction starts with RED commit `9defcace` and GREEN
commit `fdadf9aa`. Truthful pre-resource absence starts with RED commit
`36c72d23` and GREEN commit `d2ee3b68`. The latest independent Spec/security
review then rejected three authority gaps. RED `deabfaff` proves writable Gate
proof exposure, start-before-ownership, cleanup of unverified IDs, missed
headless-shell names, and unreadable `/proc`; GREEN `07aa6053` closes those
seams. Exact-image RED `f864846d` exposed the old public-socket harness, GREEN
`9d3a69d6` moves that harness to the private Broker socket, and `9ae6b419`
models the real sealed Gate files in the lifecycle fixture.
RED `44c5b1a3` proves partial capability-layout construction is terminal and
released; GREEN `1afbf9ea` moves layout mutation behind the owned lifecycle.
RED `c7849b27` proves Formal provisioning cannot conflate the sealed Agent
client with the Broker; GREEN `c197a360` adds the distinct Broker role and
per-role revision while preserving the legacy two-role narrow probe.

Not passed / not complete:

- The earlier full Python wrapper reached unrelated `meshscope` tests but the
  lightweight worktree lacks the native octree backend: 26 errors, one skip.
- Bundle freshness remains **NOT VERIFIED**: the check attempted to fetch the
  missing `esbuild` package and failed DNS. No dependency was installed.
- The first independent Standards review passed with two nonblocking design
  notes. The first Spec/security review failed with the three gaps now fixed.
  Full independent Standards and Spec/security re-review has **not yet been
  rerun** against the corrected range and exact replacement image.
- The production-shaped Docker conformance gate was **NOT RERUN** after this
  correction. Both earlier failed attempts remain preserved; the exact reviewed
  `4cdbfecf...` attempt is never reused.
- The Broker was rebuilt because the security correction changes both
  Dockerfile-copied pilot modules. The build used only the exact existing base,
  with no pull and no build network. Opt-in inspection/client test containers
  ran and were removed; no production Broker/Sidecar job or conformance host
  attempt was launched.

## Preserved production-shaped conformance attempts

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
fix resolves the capability directory before constructing the bind source and
is retained. The new review corrections and Broker artifact have not consumed
another conformance attempt.

Earlier pre-Broker design attempts and their disproven host-port hypothesis
remain documented in Git history; they are not counted as successor Broker
conformance executions.

The later, independently reviewed attempt used source HEAD
`4cdbfecf93d6ba8e266e02a976c1d7535a9988fa`, exact Sidecar
`sha256:22ff2413...b146f1`, exact Broker
`sha256:1167ad37...7ede5e`, and sealed gate
`sha256:87f7a96b...9695`. Image IDs, `linux/amd64`, full OCI revisions,
Broker base, repository locks, and empty formal job/name inventories were
verified with no pull or build. The repository entry then failed exactly once
before any Docker resource creation because it called `BrowserSidecarJob.start`
without configuring the nested gate:

`BrowserSidecarError: nested Browser Gate must be sealed before Sidecar startup`

Its immutable evidence remains at
`/tmp/browser-sidecar-formal-pilot-conformance-4cdbfecf-20260815.json`, size
1,614 bytes, SHA-256
`0767bfe051d0427efb7e199f3efeda0be2f03d3d7749df348a927722b099e857`.
The historical receipt records zero requests/contexts and
`failureCheck:absence-proof`; direct pre/post exact label and `ttc-bs-*`
inventories were empty and no new capability directory remained. This
correction makes future pre-resource receipts truthful but does not rewrite the
preserved artifact. The same SHA/handle was not rerun.

## Resource release state

- Exact successor conformance job containers/networks: absent by receipt and
  exact owner-label inventory.
- Exact `4cdbfecf...` attempt resources: none created; pre/post exact Docker
  inventories were empty and the capability tree was absent.
- Interrupted build container `16fa5b53...`: exact-owned and removed.
- Docker build intermediate containers: removed by successful builds.
- After host restart, the dedicated Colima profile registry was `Stopped` and
  the socket absent. Starting the existing profile preserved its disk and
  images; its VM, guest agent, Docker 29.5.2 socket, and exact locked image all
  became usable. The CLI registry reports `Broken` because the start frontend
  was cut off by its execution ceiling after boot completion; no profile
  rebuild, deletion, or image mutation was performed.
- Historical reviewed/superseded images, including the provenance-invalid
  `sha256:f84b1ae3...21432`, and final Broker image
  `sha256:db02c3ff...7e819`: retained; no image deletion was authorized. The
  first full-context `fd4a9db9...` build was rejected by byte-parity extraction
  because the legacy builder admitted working-tree bytecode; its image
  `sha256:51137e53...62bb` remains retained and is not an accepted artifact.
- The prior byte-parity extraction residue and `fd4a9db9` build contexts under
  `/tmp` were absent after the host cleared ephemeral storage. This continuation
  did not chmod, retry deletion, or reuse those paths.
- Final read-only Docker inventory found no `ttc-bs-*` or Browser Sidecar
  container/network residue after the exact-image tests.
- No unrelated Docker resource was adopted, stopped, relabeled, or removed.

## Deferred interaction and required next action

Return for full independent Standards and Spec/security review of
`90bc24cf8860125b158c5f04ddc5dfd65efbcb39..HEAD`. The owner has not
self-approved either axis. If both axes accept the corrections and exact new
artifact, execute the already bounded one new clean-SHA local conformance
attempt using Broker
image `sha256:db02c3ff8e5784c253eca924bd48043a4612da52fef0cbc0300f723f18d7e819`.
Do not reuse either failed SHA/handle.

Before paid CVM closure, Formal preparation must supply all three ordered roles:
Sidecar, sealed Agent client, and distinct Broker. The Broker is independently
bound to its own exact source revision and retained runtime image ID. The legacy
two-role form remains only for the provider-free narrow capability probe. No CVM
provisioning or run has been performed at this checkpoint; the bounded one-run
authorization remains gated on fresh independent re-review.

No CVM/Venus/provider/model request, push, merge, tracker mutation, dependency
installation, retained-handle access, unrelated cleanup, or image deletion has
been performed in this continuation checkpoint.
