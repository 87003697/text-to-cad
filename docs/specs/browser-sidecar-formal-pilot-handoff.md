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
- One fixed non-browser host executable is the only non-finding mask:
  `/usr/bin/sudoreplay` is pre-scanned strictly on the host, then bound to
  `/dev/null` only inside the nested bwrap namespace because it is executable
  but unreadable after `--cap-drop ALL`. The Agent does not require it, the
  nested scanner cannot safely audit its bytes without capabilities, and all
  other required host and nested `lstat`/`open`/`read`/`scandir` results remain
  fail-closed. This does not change the two-image Sidecar/Broker boundary or
  add capabilities to the bwrap Agent.
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
  `sha256:45ae12bec861c7432c9dae91c96335ca88bb5e720e136a534f4115a576e49270`
- Broker OCI revision:
  `091b9d3b95f2b7797c1cac9414f05439923a439c`
- Deterministic sealed Browser Gate zipapp for the current scanner source:
  `sha256:761076c4a3d46fd36d0b5e8992c717fbef586aa68f097556804b4b299e4fe6df`
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

- Exact locked-image extraction, real packaged-client gate, and image-sealed
  surface discovery: **3 tests, OK**.
- Focused Browser Sidecar, conformance host, sealed-image contract, surface
  scanner, nested gate, runner, and public renderer suites: **123 tests, OK**
  (**3 opt-in image tests skipped** in this non-Docker aggregate and passed
  separately above).
- Global policy gate: **274 tests, OK**, with the same 3 opt-in image tests
  skipped there and passed separately against the exact locked image.
- CVM Sidecar prepare/provision/probe suite: **48 tests, OK**, including both
  legacy two-role and Formal three-role provisioning receipts.
- Affected meshshot profile/renderer: **11 tests, OK**.
- Public `mesh-compare` preview CLI: **8 tests, OK**.
- `npm --prefix packages/meshshot test`.
- `scripts/dev/setup-symlinks.sh --check`.
- Lockfile-pinned local dependencies only, followed by current `cad`,
  `cad-viewer`, `implicit-cad`, `mesh-compare`, plugin, and derived-version
  bundle checks. No lockfile changed and no unpinned temp dependency was
  installed.
- `python -m py_compile` for changed Python and focused tests.
- Full-range `git diff --check`.
- Clean networkless/no-pull Broker build and exact read-only image inspection.
- Superseded image `02533b89...b0df105` passed once, then two later isolated
  aggregates reached the same 180-second discovery limit after both earlier
  tests passed. A same-boundary diagnostic proved the fixed 0.5 CPU allocation
  was causal: changing only the discovery role to 1 bounded CPU completed the
  scan in 98.743 seconds. The first corrected image carried a nonexistent
  expanded revision and was rejected by the commit-existence gate despite
  passing its exact tests. Superseded image `cdc77541...0c7e34` completed the
  aggregate in 113.840 seconds before its conformance job exposed the
  daemon-share gap. Superseded image `7ee52e2f...7b60fc` completed **3 tests,
  OK** in 131.488 seconds. Superseded image `39f1c3bb...e2f7b10` completed **3
  tests, OK** in 116.871 seconds. Superseded image `ccb52638...ae8b5fe`
  completed **3 tests, OK** in 115.831 seconds. Superseded image
  `a3f0e103...8c92b68` completed **3 tests, OK** in 115.607 seconds. Final image
  `45ae12be...6e49270` completed **3 tests, OK** in 114.295 seconds. Both
  timeouts remain recorded rather than reclassified.

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
RED `4cccc4b4` proves the Mac conformance host cannot require the Docker daemon
to share a canonical `/private/*` temp path. GREEN `d1f69669` moved injection
to an owner-verified stopped container; real-image RED `073fbf49` then proved
Docker rejects that copy against the read-only root filesystem. GREEN
`3fdecf50` seals the fixed Gate, contract, and surface scanner directly in the
revision-bound Broker image, eliminating both host bind and container write.
GREEN `ab17368e` keeps the fixed image-only scanner in its separately bounded
root role so it can traverse the sealed read-only Linux surface; the actual
render client remains the host UID/GID under the same networkless isolation.
Real-image inspection then exposed standard Linux cross-root aliases such as
`/usr/lib/ssl` links into `/etc/ssl` and systemd links to `/dev/null`. GREEN
`ffe3d652` closes those aliases over the complete declared read-only scan-root
set while preserving the default rejection of undeclared-root escapes and
cycles. Real-image GREEN then exposed immutable Ubuntu package/document links
whose targets are intentionally absent. RED `e54a741f` proves the generic
scanner still rejects dangling links and browser-shaped aliases. GREEN
`aa09da3e` permits only non-browser dangling aliases whose resolved lexical
target remains inside the separately declared immutable image-root closure;
the host/mounted-surface default remains strict. Real-image RED then exposed
the standard `/etc/mtab -> /proc/mounts` and `/var/run -> /run` aliases. GREEN
`f7444732` adds `/run` to the independently scanned image closure and permits
only the exact proc mounts file alongside the already fixed `/dev/null`
endpoint. RED `2f846bf0` then proves that a same-root link may traverse a second
link into another declared root and that cross-root `package.json` markers
must still be read. GREEN `05f00478` resolves the complete immutable chain
canonically, reopens the final regular inode no-follow for marker inspection,
and canonicalizes parent aliases without accepting a symlink as the declared
root itself. The resulting full traversal then exposed the capability-dropped
root discovery role's fresh `/home/pwuser` tmpfs still owned by the later
client UID. RED `ecafccaa` proves the fixed role/home mismatch; GREEN
`ba7a2423` binds the fresh home UID/GID to the selected fixed runtime role while
leaving the actual client role unchanged.
The complete image traversal then reached pre-existing private home trees that
the capability-dropped discovery role could not inspect. RED `d26c5e4a`
requires only the fixed root discovery role to carry read/search traversal;
GREEN `f50f2081` grants it only `DAC_READ_SEARCH` while retaining no network,
a read-only root filesystem, no-new-privileges, and the sealed manifest-only
entrypoint. The actual client remains capability-free.
The newly readable runtime surface then exposed Ubuntu's standard
`/run/shm -> /dev/shm` alias. RED `21258622` proves the declared closure does
not accept it accidentally; GREEN `39466404` permits only the exact Docker shm
endpoint and explicitly keeps the broader `/dev` root forbidden.
The resulting full scan then reached Debian's standard `/usr/bin/X11 -> .`
compatibility alias. RED `e15b9e42` distinguishes that inert directory alias
from a browser-shaped alias to the same directory and retains the existing
real-cycle rejection. GREEN `fb74868a` skips the inert graph edge while still
emitting an exact mask for a browser-shaped self alias.
The next full scan exposed the standard alternatives round-trip
`/usr/bin/awk -> /etc/alternatives/awk -> /usr/bin/mawk`. RED `e2937643`
proves both the inert chain and browser-shaped equivalents; GREEN `5d4649db`
routes every lexically cross-root hop through the canonical declared-root
resolver even when the final inode returns to the source root.
The final Spec/security review then found that the prior canonical resolver
validated only the final inode, root invocation could grant discovery
capability to the actual client, cleanup failure could lose terminal
precedence, and the image-only dangling exception contradicted the strict
contract. RED `18e307e0` proves all four gaps plus Broker-image sanitization.
GREEN `974b000f` resolves and validates every intermediate hop, removes the
dangling bypass and deletes inert dangling links in the immutable Broker build,
uses an explicit discovery-only capability flag, and makes cleanup failure
dominate the primary workload result. Real-image tracing then exposed standard
`/bin`, `/sbin`, `/lib`, and `/lib64` compatibility hops; RED `715d2f1f` fixes
that exact allowlist while rejecting `/`, and GREEN `0755437b` adds only those
fixed aliases to the declared closure.
Two repeat exact-image timeouts then proved that the exhaustive immutable scan
could not reliably meet its 180-second host bound at 0.5 CPU on the fixed x86
Colima. RED `5b976731` binds 1 CPU only for the sealed discovery role while
retaining 0.5 CPU for the actual client; GREEN `e4e5f67b` implements that
bounded distinction without changing network, filesystem, memory, PID,
capability, or timeout policy.

Not passed / not complete:

- The earlier full Python wrapper reached unrelated `meshscope` tests but the
  lightweight worktree lacks the native octree backend: 26 errors, one skip.
- Standards and Spec/security both passed clean HEAD `70b88271`. Its one
  production-shaped conformance job then exposed the remaining dedicated
  Colima mount prerequisite below. Fresh review has **not yet been rerun**
  against that localized correction and exact replacement image.
- The failed `70b88271` conformance receipt is preserved and will not be
  retried. A successor conformance job requires the fresh dual review and a
  distinct clean SHA.
- The Broker was rebuilt because the host correction changes the
  Dockerfile-copied conformance module. The replacement build used only the
  exact existing base, with no pull and no build network.

## Preserved production-shaped conformance attempts

Dedicated Colima profile: `browser-sidecar-prototype`, `linux/amd64`, Docker
socket
`unix:///Users/zhiyuanma/.colima/browser-sidecar-prototype/docker.sock`.

Dual-reviewed clean HEAD `70b88271744523941b140dd49b38eb16a7dcd3a3`
used exact Broker `sha256:cdc77541...0c7e34`. Its one job completed sealed
surface discovery and exact Sidecar start/readiness, then failed closed at
Broker create with `failureCheck:docker-create-status`: the dedicated profile
had `mounts:null`, so its VM could see neither the canonical `/private/tmp`
capability source nor any host home path. It accepted zero requests/contexts,
published `absenceProved:true` and `retryAllowed:false`, cleaned the exact
Sidecar/network, and left empty labeled inventories. Immutable evidence is at
`/tmp/browser-sidecar-formal-pilot-conformance-70b88271-20260816.json`, size
1,599 bytes, SHA-256
`e39bf578ca870f263e3b80319a36cdec4e2ff544b5dfc412440ce930b342e7eb`.
RED `c09e9ad7` binds the conformance capability to one daemon-shared evidence
parent. GREEN `55e8727a` accepts only an existing, canonical, current-uid,
mode-`0700` parent and retains random child ownership/cleanup. The dedicated
profile now mounts only `/Users/zhiyuanma/.ttc-bs` writable at the same path;
the VM observes mode `0700`. No broader `/tmp`, repository, or home mount was
added. The failed SHA and evidence path will not be reused.

Independent review then found that the host discarded the caller's original
parent spelling after resolution and could publish later evidence through a
retargeted alias. RED `a2b10bc9` proves the split-authority path; GREEN
`5a3d20a9` requires the supplied parent to equal its canonical resolution.
Spec/security re-review found that publishing into a rejected directory was
itself unsafe or impossible. RED `7734f992` fixes the boundary contract and
GREEN `6aed1817` rejects invalid roots before an attempt or evidence write,
while a Docker-resolution failure after trusted-root admission still publishes
exact-absence, non-retryable terminal evidence. The exact rebuilt Broker is
`sha256:ccb52638...ae8b5fe`. A further lifecycle review found that a capability
layout failure after admission cleaned and wrote its job receipt only inside
the temporary experiment tree. RED `63a5fc48` proves that loss; GREEN
`999d3027` carries the already-closed receipt on the factory error and publishes
it unchanged at the trusted canonical evidence target, preserving its cleanup
and absence classification. The exact rebuilt Broker is
`sha256:a3f0e103...8c92b68`. Spec/lifecycle review then found that an
ordinary construction error still preceded a simultaneous cleanup error in the
single failure marker. RED `d208cf22` combines partial construction with failed
Broker-directory cleanup. GREEN `091b9d3b` makes the first cleanup error
dominate ordinary startup/construction failures while retaining explicit
signal and positive-retention precedence. The exact rebuilt Broker is
`sha256:45ae12be...6e49270`.

Dual-reviewed clean HEAD `2dbcd7c17043d7bbd3f57844cd18884815b8387b`
then consumed its one authorized Colima conformance attempt with exact Broker
`sha256:45ae12be...6e49270`. Exact Sidecar readiness passed, but the Broker
exited before readiness with `failureCheck:broker-terminal-evidence`. The
receipt records zero accepted requests/contexts, exact Sidecar closing and exit
zero, `absenceProved:true`, and `retryAllowed:false`; labeled container/network
inventories were empty afterward. Immutable evidence is at
`/Users/zhiyuanma/.ttc-bs/conformance-2dbcd7c1-20260816.json`, size 1,595 bytes,
SHA-256 `ae5995f30eecd4c5ac4ba419777a89aa3512d292522a8b2a7aec8e2e0f453830`.
The SHA and evidence path will not be reused.

A bounded browser-free diagnostic used the same formal uid/image and only the
dedicated shared root. Python and Playwright import succeeded, but binding the
Unix socket on the Colima host-shared filesystem failed with `OSError: [Errno
95] Operation not supported`. The exact empty diagnostic child was removed.
This proves an environment capability limit, not another image-content defect:
the current host-to-VM shared-filesystem transport cannot carry the required
Unix socket. No docs-only SHA is new authority to retry the same design.

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

The later reviewed `5266c60c` candidate used exact Broker
`sha256:db02c3ff...7e819`. Its fresh job failed closed before any Docker
resource was created because the surface-discovery container tried to bind the
Mac-resolved `/private/tmp/.../browser-gate-discovery.pyz`, which the dedicated
daemon does not share. The terminal receipt records `absenceProved:true`, zero
accepted requests/contexts, and `retryAllowed:false`. Its immutable evidence is
preserved at
`/tmp/browser-sidecar-formal-pilot-conformance-5266c60c-20260816.json`, size
1,592 bytes, SHA-256
`4b6b0aa87e55be0bf52fbc0fabc30a4e823e0a53f23b7190445c2f51d0650c26`.
Pre/post exact container and network inventories were empty. The same evidence
path and failed candidate are not reused.

An earlier independently reviewed attempt used source HEAD
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
- Exact `70b88271...` attempt resources: the terminal receipt and direct
  post-run label inventories prove the Sidecar/network were removed and no
  Broker was created.
- Exact `4cdbfecf...` attempt resources: none created; pre/post exact Docker
  inventories were empty and the capability tree was absent.
- Exact `5266c60c...` attempt resources: none created; the terminal receipt and
  direct post-run inventories both prove absence.
- Interrupted build container `16fa5b53...`: exact-owned and removed.
- Docker build intermediate containers: removed by successful builds.
- After host restart, the dedicated Colima profile registry was `Stopped` and
  the socket absent. Starting the existing profile preserved its disk and
  images; its VM, guest agent, Docker 29.5.2 socket, and exact locked image all
  became usable. The CLI registry reports `Broken` because the start frontend
  was cut off by its execution ceiling after boot completion; no profile
  rebuild, deletion, or image mutation was performed.
- Historical reviewed/superseded images, including the provenance-invalid
  `sha256:f84b1ae3...21432`, superseded `sha256:db02c3ff...7e819`, and rejected
  `sha256:79929990...eba23`, and superseded `sha256:7e13037b...ded051`, plus
  rejected `sha256:a61e62b8...1e6f2`, superseded
  `sha256:86aaef85...bda261`, and superseded
  `sha256:34925fd0...f3c628`, and superseded
  `sha256:62574062...9af2c0`, and superseded
  `sha256:915bf400...f751624`, superseded
  `sha256:20dc8256...1ee84c7`, `sha256:dd126e36...dfb1f9a`,
  `sha256:23294596...16eaed`, `sha256:41462957...5bf1a2d`, and superseded
  `sha256:b7058cb6...bfb5b1`, plus superseded Broker image
  `sha256:02533b89...b0df105`, rejected-provenance
  `sha256:a286f43f...8b9249`, superseded Broker image
  `sha256:cdc77541...0c7e34`, superseded Broker image
  `sha256:7ee52e2f...7b60fc`, superseded Broker image
  `sha256:39f1c3bb...e2f7b10`, superseded Broker image
  `sha256:ccb52638...ae8b5fe`, superseded Broker image
  `sha256:a3f0e103...8c92b68`, plus final Broker image
  `sha256:45ae12be...6e49270`, are retained; no image deletion was authorized.
  The
  first full-context `fd4a9db9...` build was rejected by byte-parity extraction
  because the legacy builder admitted working-tree bytecode; its image
  `sha256:51137e53...62bb` remains retained and is not an accepted artifact.
- The prior byte-parity extraction residue and `fd4a9db9` build contexts under
  `/tmp` were absent after the host cleared ephemeral storage. This continuation
  did not chmod, retry deletion, or reuse those paths.
- Final read-only Docker inventory found no `ttc-bs-*` or Browser Sidecar
  container/network residue after the exact-image tests.
- The persistent dedicated profile mount root `/Users/zhiyuanma/.ttc-bs` is
  intentional local infrastructure, current-user owned and mode `0700`; no
  per-job capability child remained after the failed attempt or unit tests.
- No unrelated Docker resource was adopted, stopped, relabeled, or removed.

## Git milestone and tracker reconciliation recommendation

No Git remote or tracker mutation is authorized. The precise local milestone
is:

- baseline: `90bc24cf8860125b158c5f04ddc5dfd65efbcb39`;
- dual-reviewed implementation/docs candidate:
  `2dbcd7c17043d7bbd3f57844cd18884815b8387b`;
- locked Broker implementation revision:
  `091b9d3b95f2b7797c1cac9414f05439923a439c`;
- immutable Broker image:
  `sha256:45ae12bec861c7432c9dae91c96335ca88bb5e720e136a534f4115a576e49270`;
- the evidence-only handoff commits after `2dbcd7c1` are documentation
  milestones, not new conformance authority.

Recommended tracker reconciliation for
`browser-sidecar-formal-pilot-integration` is **keep open / Formal pilot
pending**, not completed and not failed implementation. Record the local
regression and dual review as passed, attach the immutable Colima failure
receipt, and record `host-shared Unix socket unsupported` as the local
environment limitation. The unchanged transport subsequently passed the
native-Linux CVM three-role gate below. Keep Venus pilots, canonical
Workspace/CAD/Viewer/eight-view review, and final receipts as `Not Run`. A
separate follow-up may evaluate a Colima-native Docker-volume or network
transport, but it must not silently weaken the current private Unix socket
authority or convert this failed SHA into retry authority.

## Deferred interaction and required next action

Standards and Spec/security both accepted exact implementation HEAD
`2dbcd7c17043d7bbd3f57844cd18884815b8387b` and Broker
`sha256:45ae12bec861c7432c9dae91c96335ca88bb5e720e136a534f4115a576e49270`.
Its one Colima attempt is closed and must not be retried. A future local pass
requires a separately reviewed transport design that does not place a Unix
socket on Colima's host-shared filesystem. The next valid unchanged-transport
boundary is native Linux CVM, where the private capability directory and Unix
socket share one native filesystem.

Formal CVM preparation supplied all three ordered roles: Sidecar, sealed Agent
client, and distinct Broker. The Broker is independently bound to its own exact
source revision and retained runtime image ID. The legacy two-role form remains
only for the provider-free narrow capability probe.

The repository `cvm-push` workflow deployed clean source
`bdf662f9d56e53d30e1bbacae8136be82ea45eec` and passed its remote runtime hash
checks. Its receipt SHA-256 is
`f84f04aee5b3e0d0ec15db30cd7a348fbc3f8fab9e572ca43c845476ddafeb70`;
the remote Git base was `no-git`, so the receipt's source identity, not a remote
checkout HEAD, is authoritative.

Three-role handle `cvmsp-289e369c94709037b2af7135` prepared a 1,084,925,952
byte archive with SHA-256
`c54b7d9743923aab03ca7bfe55942ee6a8f661bb8e9872d3c5912f6adc1f75a6`.
Provision verified the same archive remotely, retained three distinct runtime
image IDs in Sidecar/client/Broker order, and proved the archive, incoming
directory, and remote prepare receipt absent. The local provision receipt is
`.cvm-sidecar-probes/cvmsp-289e369c94709037b2af7135/provision.json`, SHA-256
`b9e970f28da9b6a9d47470e8bded8517cf67ce6352f3500bb89595b5c20ddbc3`.

The same handle's one-shot probe succeeded with fixed request SHA-256
`b155c2ac8a5396971825cd09626f75510d2669fbcdd669f9e1cfe9ce41fdf3a6`.
It proved one connected context and page, blocked external egress, no visible
browser executable or source alias, exact closing, exit zero, no cleanup
errors, and labeled container/network absence. Its local receipt is
`.cvm-sidecar-probes/cvmsp-289e369c94709037b2af7135/probe.json`, SHA-256
`663a67f04a2222e37709b7c726bebd2e476a95f3de0a34efd885ab6c499854b5`.
Both terminal operations record `retryAllowed:false`; this handle must not be
reused.

After a fresh payload-specific grant, the canonical snapshot for group
`20260816-094800-browser-sidecar-cup` uploaded successfully and became visible
through the Mac mount. Its `HEAD.sha` is
`d6eb1d167d3f48ff1065df34d73413c05f7c6d91`; `dirty.diff` and `untracked.txt`
are both empty. A second incremental `cvm-push` aligned the deployed source to
that exact clean HEAD and passed remote runtime verification. Its receipt
SHA-256 is
`755f7f396047ef03ce6f6f5e3676827c87993facdea691a598d2b2551fa3ec40`.

Exactly one new Cup pilot was submitted:
`20260816-094800-browser-sidecar-cup/20260816-015329-cup_cup_033`.
It reached terminal `failed` in the monitor with `process_exit_code:1`,
`runner_final_status:1`, last checkpoint `pilot: initial commit`, and tap
availability `pending`. The handle must not be resubmitted. Default safe
`cvm-pull --exp` confirmed `final_status=1` and one retained
`run/.codex-upper`; by contract it uploaded nothing and preserved the complete
CVM postmortem. No canonical Workspace, CAD, Viewer/eight-view artifact, or
rollout usage receipt is locally available, so no model invocation or exact
incremental cost may be claimed from this evidence. The short pre-workload
failure is consistent with, but does not by itself prove, the open integration
risk that the paid runner still addresses the Mac-fixed image IDs while CVM
provision retained different backend runtime IDs. Do not submit a successor
until the retained postmortem can be reviewed without violating its retention
boundary or a separately reviewed provisioning-receipt-to-runner binding closes
that ambiguity.

The default pull contract exposed a tooling gap: it could either skip the
failed experiment or upload it and clean the CVM source, but could not publish
the postmortem for review while retaining the failure authority. Commits
`e06fa886`, `8aaf15d2`, `62e45b14`, and `91a82591` add and seal explicit
`--include-byproducts --retain-cvm-source`. The final range
`fce23038...91a82591` passed 28 focused/contract tests plus independent
Standards and Spec/security review with zero findings. It rejects aliased
outputs/group/exp roots before manifest access, never follows or counts child
symlinks, distinguishes uploaded from verified-existing S3 state, proves mount
visibility, reports retained CVM source separately, and never enters cleanup
under the retain policy.

After a fresh payload-specific grant, the retained-postmortem publication
succeeded through the reviewed `cvm-pull` path. It uploaded and then verified
31 regular files at
`s3://arcwm-code-us-west-2/ericzyma/text-to-cad/outputs/20260816-094800-browser-sidecar-cup/20260816-015329-cup_cup_033/`,
proved the same count visible through the Mac mount, followed no symlink, and
retained the complete CVM source directory.

The published postmortem closes the earlier runtime-image hypothesis. The job
receipt binds the expected Sidecar, Broker, base, and source identities, records
zero accepted requests and contexts, proves exact absence, and closes at
`sidecar-readiness`. `run/stderr.log` records
`cannot close mounted Agent browser surface` before Sidecar startup. There is
no `run/rollout.jsonl`, `workspace.json`, `step_index.json`, `notes.md`, or
`final/manifest.json`; the Venus workload never started and the verified
incremental model cost for this handle is `$0`.

The failure exposed a host-runner integration defect rather than another image
defect. The generic scanner already supports links across a complete explicitly
declared root closure, but `prepare_nested_browser_gate` scanned each read-only
mount as if it were an isolated root. Normal Linux aliases between separately
declared immutable roots were therefore rejected. Clean implementation commit
`865569f154bf4e12a40e58c37d38140af636c470` passes the 103-test focused
Browser Sidecar/runner set and the full Python global suite, plus bundle and
development-symlink checks. Independent Standards and Spec/security review both
report zero findings. It supplies every read-only source root as the scanner's
declared closure while leaving both writable mounts on the strict no-exception
path; dangling, cyclic, uninspectable, and undeclared-root escapes still fail
closed. The change is host-runner-only and does not require another image.

The repository `cvm-push` workflow then deployed exact clean source
`865569f154bf4e12a40e58c37d38140af636c470` and passed remote runtime hash
verification; the remote Git base remains `no-git`, so the push receipt source
identity is authoritative. A new snapshot was not created: the attempted new
group `20260816-102100-browser-sidecar-cup-r2` was rejected before upload by the
external-action gate because the prior snapshot grant named only the old
payload/destination. Do not reuse the old `d6eb1d16` snapshot with the new
runner or submit a second paid pilot until a fresh exact snapshot destination
is authorized and verified.

No merge, Git push, tracker mutation, unrelated cleanup, historical image
deletion, or second paid submission has been performed through this checkpoint.
Only the lockfile-pinned worktree-local docs dependencies were installed to run
the successful lint and production build. Submission usage is 1 of 10,
effective paid model executions are zero, and verified incremental spend is
`$0` of the authorized `$500` ceiling.
