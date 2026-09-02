# Immutable Workspace helper

scripts/mesh-to-cad-workspace is the public workflow-state boundary for the
canonical repair protocol. It is self-contained and uses no imports from peer
skills or the repository root.

## Agent Surface

`scripts/mesh-to-cad-agent-surface` is the separate Agent-facing seam. Its
`handler.py` is the shared implementation used by the stdin CLI and the
minimal newline JSON-RPC/MCP adapter. The handler receives supervisor-injected
ports; it does not discover or import Workspace, reference, CAD, Git/LFS, or
authority implementations.

`__main__.py` is the one-object stdin CLI entrypoint; `mcp.py` is the newline
JSON-RPC adapter. Both are intentionally inert without W4-supplied ports.
The adapter supports the fixed MCP protocol version `2025-06-18` and the
`initialize`, `tools/list`, and `tools/call` lifecycle subset; the repository
environment has no maintained MCP Python SDK, so W3 uses a bounded protocol
fixture rather than adding a dependency.

Agent requests use the closed `mesh-to-cad.agent-intent/1` envelope and only
the following intents: `workspace_status`, `start_attempt`,
`run_candidate_tool`, `submit_step_zero`, `evaluate_repair_draft`,
`submit_repair`, `abandon_repair_attempt`,
`inspect_repair_targets`, `observe_target_section`, `select_and_finalize`, and
`observe_reference`.
Workspace, Attempt, candidate,
plan, evidence, selection, notes, and reference values cross this seam only as
opaque handles. `run_candidate_tool` accepts a supervisor-registered operation
handle, never an argv or command string. Reference observations are limited to
the fixed W2 summary operation. Paths, raw geometry, authority
documents, exceptions, and secrets are rejected or removed from the response
contract. Each intent has its own closed argument and result projection; there
is no generic successful-result bag.
The `observe_reference` port projects W2's summary result into that
closed projection before it reaches the Agent; the W3 handler does not pass a
generic W2 dictionary through.
`start_attempt` has two exact argument variants: an initial plan with no parent
field, or a repair plan with only an opaque `parent_step_handle` returned by a
previously published Measured Step; the supervisor resolves that handle to a
W1 step ordinal internally and derives the intended step from that branch and
parent. Numeric parent ordinals and cross-run or wrong-kind handles fail
closed at the Agent Surface boundary before any Workspace call.
`start_attempt` returns one additional opaque
`capability_bundle_handle`. It is bound only after the Workspace returns the
actual Attempt and intended step. Use it for Step 0 candidate-tool execution;
Repair draft evaluation performs its own private canonical build. Bootstrap
contains only run-level capabilities and the fixed maximum budget, never
predicted global Attempt IDs.

`submit_step_zero` accepts only the opaque workspace, Attempt, and candidate
handles. Repair authors source and assessment, then uses
`evaluate_repair_draft` with an Attempt bound ticket. The supervisor snapshots
those inputs into a fresh private stage, runs the fixed canonical build there,
and retains an immutable draft with closed feedback. `submit_repair`
accepts only the workspace, Attempt, and selected draft handles, while
`abandon_repair_attempt` retires the Attempt without consuming a Cycle. The
Agent never selects an evidence handle, path, or filename. Eight evaluations
are admitted per intended step across up to three Attempts; invalid and stale
tickets consume no slot, an admitted provider failure consumes one slot, and a
completed ticket replays its cached result. Concurrent calls with the same
ticket share that one result and spend one slot. No ninth ticket is issued.
Repair always permits abandonment while its Attempt is active. Submission is
permitted only after at least one successful retained draft exists; evaluation
is permitted only while a valid next ticket exists. A later failure or exhausted
budget does not remove an earlier retained draft. If abandonment overlaps an
admitted evaluation, the supervisor lets that single flight settle before it
revokes the Attempt and removes every retained private stage; no draft from the
retired Attempt remains callable.
W1 copies only the Agent-authored source and assessment into each private
draft stage. The supervisor produces the fixed candidate measurement there,
and the real Repair evidence provider produces measurement, preview, diff,
and source-change evidence. None of those filenames or stage paths is an
Agent contract.

Draft feedback is the closed `mesh-to-cad.repair-draft-feedback/1` projection
of the frozen parent Active frontier and draft Active frontier. `before` and
`after` contain exactly missing/excess surface counts and signed `delta` is
`after - before`. `target_change_preview` partitions exact public
`{kind,bounds_canonical}` identities into `resolved`, `persisted`, and `new`,
with at most eight returned items per partition and explicit total/remaining
counts. It publishes no numeric depth, target key, mask, component, path, or
Depth-8 result. Evaluation publishes no Step, Cycle, VoxBlame index, Final
Delivery, or parent-history mutation. Submitting a retained draft promotes its
exact frozen candidate, assessment, and real evidence without rebuilding or
rerunning the provider, then publishes exactly one Cycle.

`inspect_repair_targets` accepts exactly a returned opaque `step_handle` and a
non-negative page-start `offset`. W1 reads that historical Measured Step's
committed full Repair Target authority and returns at most eight consecutive
public `{rank, kind, bounds_canonical}` items. `next_offset` is the next
consecutive page start or `null`; target keys, masks, paths, and raw octree
identities remain behind the supervisor. Paging is available only between
Attempts, so an active Attempt cannot mix target observations from another
workflow state. An empty target set has one valid empty page at offset zero.

`observe_target_section` accepts exactly a returned historical `step_handle`
and one public target `rank`, and is available only between Attempts in the
preterminal state. W1 resolves that rank through the same committed directional
target projection as paging, while W3 privately binds the canonical Reference
and that Step's committed candidate mesh and surface snapshots. Its
`mesh-to-cad.target-section-observation/3` result contains the rank, Reference
and candidate `core` profiles, and `local_occupancy`. Each core has a triangle
count and fixed X/Y/Z eight-slab values. For missing/excess targets,
`local_occupancy` contains separate Reference and candidate `[x][y][z]` 3×3×3
Active-Depth cubes centered at `target:[1,1,1]`; booleans distinguish occupied
and in-frame empty cells, and matching `null` cells mark the canonical-frame
boundary. Exterior targets retain both cores and return `local_occupancy:null`.
The cubes describe only target-local lattice adjacency; connectivity, semantic
identity, and thickness remain outside this observation.
It mints no handle, accepts no arbitrary bounds, and exposes no target key,
mask, path, Depth-8 identity, Component, or capability detail.

For an unaccepted Selected Step that still has public Repair Targets,
`no_feasible_strategy` finalization requires one successfully closed
`observe_target_section` response for that exact Step. W4 records this
run-private receipt only after the Agent Surface validates the response and its
production transport successfully writes and flushes it. It is not
Agent-authored evidence and is never serialized into Final Delivery. A
receipt for another Step cannot authorize the selection. A missing receipt
returns `state_conflict` before finalization staging or publication; the same
selection and notes handles remain usable after `workspace_status` and a valid
observation of the Selected Step.

The standalone adapters have no supervisor discovery fallback: without W4
ports they return a closed `supervisor_unavailable` error. W4 owns the concrete
ports and the process/filesystem wiring that binds these handles to Workspace,
candidate execution, and the Restricted Reference Capability.

For runner-level isolation checks, `scripts/pilot/runner.py run` accepts the
trusted `--agent-candidate-dir <external-dir>` option. It is intentionally not
an Agent argument; the outer runner validates that directory and binds it at
`/candidate` while omitting experiment and input mounts.

The production Toys4K launcher enables this seam and mounts the runner-owned
Unix bridge at the fixed `/run/mesh-to-cad-agent-surface.sock` endpoint plus
the fixed `/agent-surface/client.py` adapter. The Agent receives opaque
capabilities in `/candidate/bootstrap.json`; that file contains no host path,
raw mesh, Workspace identity, or terminal handoff location. The bridge serves
both one-object CLI envelopes and the W3 MCP lifecycle over the same handler.
Before starting the bridge, the outer runner uses the public Meshscope
Canonical Reference preparation API and Workspace `init` CLI with the raw
input kept exclusively on the trusted side. It then supplies the real CAD
rebuild entrypoint, geometry entrypoint, and closed tool registry to the
Supervisor; Final Delivery is not a test-only injected path.

The trusted pilot runner supplies those ports through
`scripts/pilot/workspace_supervisor.py`. Each run gets a fresh handle registry;
handles are bound to the current run and Attempt, and one-shot candidate
operation handles reject replay. Candidate commands are registered by the
runner and execute with a fixed argv, candidate-only cwd, and a small
environment allowlist. The Agent never supplies argv, a cwd, a Workspace
path, or a reference path. When the runner selects the candidate-only bwrap
seam, the workload sees only the fixed `/candidate` mount; the experiment,
input mesh, output authority, and outer Git tree are not mounted there.
The outer Agent sandbox mounts the supervisor's external candidate root at
`/candidate`; the fixed current-attempt subtree is `/candidate/work`, and
the nested candidate-tool bwrap binds that host subtree to `/candidate` so
the registered operation argv stays candidate-relative
(`source/model.py`). No Attempt identifier is encoded in any Agent-visible
path. `start_attempt` securely resets `/candidate/work` before binding the
opaque candidate handle to it; a submit retires the Attempt, revokes all
attempt-scoped handles, and clears `/candidate/work`. Supervisor-owned
plan, selection, and notes control files live at `/candidate/*.json`
outside `/candidate/work` so that the reset never destroys them. Stale
cross-attempt candidate handles and any forged `attempt-000001` sibling
under `/candidate` are ignored: only the fixed `/candidate/work` binding
carries authority. For a Repair Attempt (`from_step != null`), after
`begin_attempt` returns, W1's `seed_repair_source_from_parent_step`
operation descriptor-safely copies the parent Measured Step's committed
candidate `source/` bytes into the fresh `/candidate/work/source/`; the
supervisor supplies only the external empty work tree destination and
never reads, forwards, or interprets a `steps/…` authority path.
On Linux, each registered candidate operation gets the same minimal mount
boundary and network-denied process context; hosts without that primitive fail
closed unless a test-injected executor is supplied.
Candidate staging, authority-side measurement staging, finalization staging,
terminal status, and the transfer sidecar location are obtained through public
Workspace-facade operations. The facade owns candidate ingestion, finalization
staging reset/cleanup, and sidecar placement; the Supervisor passes only a
validated external candidate source and relative capability names and does
not reconstruct `work/`, `final/`, or other authority paths. Candidate file
copies are descriptor-bounded, no-follow, regular-file-only, single-link
copies with stable metadata and a second digest check. On Windows, where the
POSIX `dir_fd` traversal primitives are unavailable, W1 uses no-follow
`lstat`/open/`fstat` identity binding for each path and checks directory
identity before and after traversal. A growing, swapped, hard-linked, or
special-file source aborts and removes partial staging.

Candidate execution receives a trusted content-addressed CAD runtime at the
stable `/runtime` mount. The runner resolves the interpreter from the pinned
uv/venv, admits only the fixed build123d/OCP distribution dependency closure
and Python loader closure, and atomically reuses an immutable cache entry
across pilots. It omits unrelated packages, `pyvenv.cfg`, editable `.pth`,
`direct_url.json`, bytecode, and metadata that contains host or checkout
paths. Sysconfig/prefix metadata and standalone libpython identities are
rewritten to `/runtime` where relocation is provable. Candidate `PATH`,
`PYTHONHOME`, cwd, argv, and output use only `/runtime` and `/candidate`
names; an unsafe external interpreter, loader, or symlink escape fails closed
before the Agent starts.
Each immutable cache entry carries a canonical relative-path/size/digest
manifest that is streamed and rechecked before reuse. Cache construction uses a
PID/boot/process-start owner lock with bounded stale-owner recovery, and keeps
only a small validated identity set. The runner holds an external live lease
for the selected identity until the Agent bridge and candidate tools stop;
retention never removes a live-leased or building entry.

W2 is constructed behind the supervisor's opaque reference handle. Only its
summary response projection crosses the Agent Surface. Raw or
canonical PLY bytes, path names, and arbitrary geometry operations do not.

After trusted Final Delivery succeeds, the outer runner asks the W1 facade to
compile terminal validation once and writes the bundle plus expected identity
to an atomic, locked handoff at
`<workspace.parent>/.internal-terminal-validation/<workspace.name>/terminal-validation.json`,
outside the Workspace tree. That external handoff is the sole trust lineage
for the transferred bundle. Exceptional rollback objects are retained as
bounded, hidden quarantine tombstones rather than deleted through a racy
pathname. The resulting `TerminalValidationLocator` is a runner/reviewer
contract and is never part of an Agent response or Agent-visible environment.
A valid existing handoff is reused on retry; an incomplete write is never
published as success.
The Workspace-local `run/terminal-validation-locator.json` is a minimal closed
discovery marker (`{schema, handoff_layout}`); it never carries the bundle or
expected identity and therefore cannot authenticate a transferred Workspace on
its own. CVM transfer preserves that marker so downstream consumers can
confirm the fixed external handoff layout even when broad disposable `run/*`
excludes are configured. Alongside the exp bytes CVM transfer publishes the
external handoff as a sibling S3 prefix at
`<group>/.internal-terminal-validation/<child>/terminal-validation.json` and
verifies byte length, SHA-256 file digest, and terminal identity match
exactly before cleaning either the exp tree or the handoff directory. A
mismatch or missing handoff retains the CVM source for investigation.
`select_and_finalize` itself only publishes Final Delivery; the outer runner
publishes its final artifact manifest first and then performs the single W1
compile/persist step.

The default pilot-review/evaluate consumer derives the external handoff path
`<workspace.parent>/.internal-terminal-validation/<workspace.name>/terminal-validation.json`
from the trusted experiment/group context and opens it through
descriptor-relative `O_DIRECTORY`/`O_NOFOLLOW` traversal. The handoff carries
the closed bundle and the terminal identity that is passed directly to W1
`verify_terminal_validation` exactly once; W1 is the only bundle identity
authenticator. The Workspace-local locator is consulted only to confirm the
minimal marker; its content is never used for verification, so a locator
forged to carry an embedded bundle plus matching identity cannot
self-authenticate. The consumer uses W1's closed graph, `review_graph`,
review facts, and evaluation facts as its structural input. Reviewer-owned
default `prepare`/`review` outputs live under `run/review/`, excluded from the
W1 inventory. Review never reruns the full Workspace validator or scans raw
attempt, step, cycle, command, or Final Delivery records. Missing or legacy
handoff data fails closed.

Invoke it with the active project Python:

    python skills/mesh-to-cad/scripts/mesh-to-cad-workspace init ...
    python skills/mesh-to-cad/scripts/mesh-to-cad-workspace begin-attempt ...
    python skills/mesh-to-cad/scripts/mesh-to-cad-workspace build ...
    python skills/mesh-to-cad/scripts/mesh-to-cad-workspace run ... -- <argv>
    python skills/mesh-to-cad/scripts/mesh-to-cad-workspace record-attempt ...
    python skills/mesh-to-cad/scripts/mesh-to-cad-workspace publish-step-zero ...
    python skills/mesh-to-cad/scripts/mesh-to-cad-workspace publish-cycle ...
    python skills/mesh-to-cad/scripts/mesh-to-cad-workspace finalize \
      --workspace <EXP_DIR> --selection <final-selection.json> --notes <notes.md> \
      --rebuild-entrypoint <registered-cad-adapter> \
      --geometry-entrypoint <mesh-compare-entrypoint> \
      --tool-registry /run/meshshot-browser/trusted-tool-registry.json
    python skills/mesh-to-cad/scripts/mesh-to-cad-workspace status ...
    python skills/mesh-to-cad/scripts/mesh-to-cad-workspace rebuild-index ...
    python skills/mesh-to-cad/scripts/mesh-to-cad-workspace recover ...
    python skills/mesh-to-cad/scripts/mesh-to-cad-workspace validate ...
    python skills/mesh-to-cad/scripts/mesh-to-cad-workspace terminal-validate \
      --workspace <EXP_DIR>
    python skills/mesh-to-cad/scripts/mesh-to-cad-workspace terminal-result \
      --workspace <EXP_DIR> --bundle <terminal-bundle.json> \
      --expected-terminal-identity <SHA256>

Every machine response is exactly one JSON object on stdout. Contract failures
return exit 2 with error.classification, error.path, and error.detail.
run returns the wrapped command's exit code.

## Publication model

- init accepts a prepared directory containing input/, setup/, and a
  closed mesh-to-cad.experiment/1 manifest.
- begin-attempt freezes either an initial plan or a validated
  voxblame.repair-batch/1.
- build resolves the sole canonical CAD launcher from the trusted registry,
  confines source, inputs, and output to the active candidate, and performs an
  unrecorded provider-free preflight before spending an Attempt command.
- publish-step-zero cross-checks the candidate mesh, formal preview,
  canonical `voxblame.summary/1` measurement, Canonical Reference, canonical
  frame, and preview profile before publishing steps/000000/. Objective facts
  are recomputed from depth-8 and exterior evidence; the preview identity is
  recomputed from its canonical metadata.
- publish-cycle publishes a marker-last transaction containing both
  steps/NNNNNN/ and cycles/NNNNNN/. Its plan identity, Region Diff edge,
  source-change evidence, assessment, ancestry, and Observable Geometry
  identities must agree.
- record-attempt publishes failed or strategy-changed Attempts without
  creating a Measured Step.
- A canonical candidate build that exits nonzero returns `run_candidate_tool`
  with `state=failed`, leaves the Attempt active for a bounded retry, and does
  not spend a published Attempt command until a build succeeds.
- If trusted Repair evidence cannot satisfy its fixed comparison contract,
  `evaluate_repair_draft` returns one admitted failure with a closed subtype.
  The failure consumes its evaluation slot but no Cycle or tool-failure budget,
  and the Attempt remains active. Completed success and failure tickets replay
  exactly. The Agent may submit any earlier retained draft or abandon the
  Attempt; it never cleans up a private draft build or `candidate.glb`.
- finalize validates Agent-owned selection evidence, copies every selected
  recipe input into isolated staging, executes the explicitly supplied
  registered CAD rebuild adapter, proves the complete source to
  primary artifact to measurement-mesh provenance chain, runs non-publishing
  VoxBlame equivalence through the supplied geometry entrypoint, renders the
  final preview, and atomically publishes `final/`. Explicit entrypoints keep
  the helper installable without locating or importing sibling skills. The
  caller's trusted registry is the explicit authority boundary: it binds the
  registered CAD adapter and VoxBlame IDs to the exact entrypoint
  digests executed. Do not generate or alter this registry from model output;
  installation/orchestration must supply it.
  `measurement.json` is an unchanged Selected Step summary; verification is a
  separate non-step artifact.
- step_index.json is a compact derived graph. rebuild-index recreates it
  from immutable step, cycle, attempt, and Final Delivery authority.

Step numbers do not imply ancestry. Every nonzero measurement, Measured Step,
Repair Cycle, Region Diff, assessment, and source-change document names the
same explicit earlier parent. This allows later cycles to branch from history.

## Bounds and recovery

The Workspace permits ten successful Repair Cycles. Each intended step permits
three Attempts, at most two of which may end as actual tool failures. Failed
Attempts consume no cycle. A successfully published geometric no-op consumes
one cycle.

build owns the reserved `build` phase; generic run rejects that phase. run
executes all other argv directly without a shell. An Attempt permits eight
commands. Each command defaults to and caps time at 1800 seconds (30 minutes), stores at most
64 KiB from each output stream using a versioned head/tail policy, and redacts known secret-bearing
arguments and Authorization headers.

Setup, Measured Step, Repair Cycle, Attempt, Final Delivery, and index writes use validated
temporary staging and atomic rename or replacement. A marker-last transaction
interrupted between Step and Cycle rename is invalid authority; recover
finishes only a staged transaction whose identities cross-check. The marker is
removed only after index publication and the scoped Git commit succeed, so a
post-rename interruption remains recoverable, including failed Attempt and
Final Delivery publication. Recovery keeps an already committed Final Delivery
or rolls an uncommitted rename back to the exact pre-finalization notes/index,
so the operation can be retried. Protocol-scoped VoxBlame paths are checked
before any authority rename. Unknown staged state fails closed.

Final Delivery contains `source/` with every recipe-declared input at its
reproducible relative path, rebuilt `artifacts/`, `build.json`, the pinned
`tool-registry.json`,
`rebuild.json`, the unchanged Selected Step `measurement.json`, independent
`verification.json`, final `preview.png`/`preview.json`, `selection.json`, and
`manifest.json`. Rebuild success never upgrades an unaccepted selection.
Source mutation, network-enabled recipes, provenance or Observable Geometry
mismatch, Agent semantic conflict, and exhausted render retries publish no
`final/`; historical artifacts and previews are never fallbacks. After the
scoped final commit succeeds, mutable `work/` contents are removed.

## Git and telemetry boundary

The experiment directory must already be a Git repository root. Initialization
installs the repository-local Git LFS hooks and adds, without overwriting
existing rules, LFS attributes for protocol binary artifacts. Publication:

- rejects pre-existing staged paths;
- stages only the paths declared by that protocol transaction;
- verifies the LFS filter before committing binary artifacts; and
- binds Workspace, Attempt, Step, Cycle, plan, candidate, and Observable
  Geometry identities through commit trailers. Final publication additionally
  binds Selected Step, inherited acceptance, and Final Delivery identity.
  Validation checks the current publishing commit for each authority path, not
  any older matching message.

`workspace.json` also freezes input and setup tree identities. Validation
recomputes them so mutation of prepared authority is reported as corruption.

The helper never uses broad staging or disables LFS filters. run/ and work/
are ignored mutable areas. Runner logs or transfer manifests are never
Workspace authority and cannot change validation, acceptance facts, ancestry,
or budget.

## Workspace facade and terminal result

Repository callers import the Workspace boundary from
`scripts/mesh-to-cad-workspace/workspace.py`. It reuses the existing helper
implementation for mutations, current validated state, recovery, and Git/LFS
publication. Callers do not import `workspace_core.py` directly.

`terminal-validate` compiles one closed, versioned in-memory bundle after a
validated terminal state. It returns the bundle and a stable
`terminal_identity_sha256` handoff; it does not write Workspace files, claim a
handoff path, create stages, or perform crash recovery. The exact content
manifest lists every immutable Workspace file with its SHA-256 and byte size;
mutable `run/` and `work/` trees and Git metadata are excluded.
`terminal-result` accepts a bundle JSON path plus the caller-supplied expected
identity and verifies closure, deterministic review/evaluation facts, and
every listed Workspace byte without rerunning complete validation or using
`.git`. Recomputed internal digests cannot pass against the original expected
identity. Publication persistence, handoff storage, and retry after process
death belong to the outer W4 runner. A hard crash may cause the runner to
compile again; exactly-once means one validator call per successful compilation
only. Any content, schema, or identity mismatch fails closed with the normal
Workspace JSON error shape.
