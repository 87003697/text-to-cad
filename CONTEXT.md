# Text-to-CAD Reconstruction and Execution

This context defines the language used by reconstruction evaluation, provider-free preview execution, and the sealed Agent runtime. It names domain concepts only; ADRs and design documents own implementation decisions.

Current implementation progress, validation gates, and deferred work are
tracked in [`docs/roadmap.md`](docs/roadmap.md). Historical plan status does not
override that roadmap.

## Reconstruction Language

**Reconstruction Spec**:
A default-on (enabled by default), Agent-authored JSON working document
describing the observable components, features, and relations hypothesized for
one Canonical Reference. A task or pilot instruction may explicitly opt out
for a controlled execution. It may be corrected in place as reconstruction
evidence changes and is not Workspace authority.
_Avoid_: CAD plan, source plan, ground truth, Workspace manifest, reconstruct-spec

**Initial Spec**:
The first Reconstruction Spec created exclusively from inspected input-mesh geometry and its Canonical Reference before initial CAD authoring.
_Avoid_: User-provided Spec, user-guided Spec, generated CAD plan

**Spec Item ID**:
A human-readable identity unique across all Components, Features, and Relations in one Reconstruction Spec. It remains stable while the Agent continues to mean the same item and is independent of source callables, CAD labels, Repair Targets, and array positions.
_Avoid_: Source symbol, STEP label, target key, display name

**Reference Component**:
A materially or functionally distinct constituent hypothesized to belong to a Canonical Reference, identified independently of any CAD assembly node or STEP label.
_Avoid_: CAD module, assembly child, mesh segment

**Reference Feature**:
A localized form or functional characteristic hypothesized to belong to a Reference Component, identified independently of the CAD operation used to construct it.
_Avoid_: Boolean operation, source helper, Repair Target

**Reference Relation**:
A first-class, independently identified, directed semantic claim connecting two Reference Components or Reference Features.
_Avoid_: CAD constraint, assembly mate, transform

**Organizational Relation**:
A Reference Relation whose reserved kind is `part_of`, `depends_on`, or `affects`.
_Avoid_: Custom relation, CAD operation, assembly constraint

**Constructive Relation**:
A Reference Relation with an open, non-reserved kind that describes a spatial or assembly claim. Its kind carries no kind-specific semantics in the MVP.
_Avoid_: Organizational Relation, registered relation type, closed relation enum

**Spec Certainty**:
An optional descriptive label on a Reference Component, Reference Feature, or Reference Relation, using `observed`, `inferred`, `hidden`, `uncertain`, or `mixed`. It is preserved for interpretation but does not control validation, repair, acceptance, or other workflow behavior.
_Avoid_: Confidence score, workflow state, acceptance level

**Canonical Reference**:
The evaluated input triangle surface after one Trellis2 max-extent normalization into the fixed `[-0.5, 0.5]^3` coordinate space.
_Avoid_: Ground truth in world units, normalized candidate

**Measured Step**:
An immutable candidate state whose preview and VoxBlame evidence were successfully published. Step 0 is the initial candidate; repair cycle N produces step N.
_Avoid_: Iteration, latest model

**Repair Cycle**:
An immutable successful edge from one measured step to another, containing the frozen plan, direct region diff, source change evidence, and Agent assessment.
_Avoid_: Iteration, attempt

**Attempt**:
A frozen-plan execution that tries to produce a measured step and may fail without consuming a repair cycle.
_Avoid_: Cycle, step

**Repair Target**:
A source-step spatial partition of surface-occupancy error, identified by a fixed exact mask and diagnostic bounds. It says where the disagreement is, not how to edit CAD.
_Avoid_: Action, command, next action

**Active Repair Depth**:
The coarsest interior occupancy depth from 1 through 8 that still contains missing or excess surface evidence for one measured step. It is recomputed by VoxBlame and is never selected by the Agent.
_Avoid_: Agent-selected resolution, acceptance depth

**Repair Frontier**:
The current measured step's repair targets grouped at its active repair depth while retaining exact depth-8 masks. It may advance, remain, or return to a coarser depth after a repair.
_Avoid_: Target queue, resolution setting

**Repair Hypothesis**:
An Agent-authored, falsifiable explanation that connects repair-frontier evidence to one plausible CAD change.
_Avoid_: VoxBlame recommendation, objective fact

**Repair Batch**:
One coherent modeling change that selects one or more current-step repair targets and maps planned edits to them.
_Avoid_: Action list, voxel queue

**Planned Edit**:
An Agent-authored CAD change within a repair batch, identified by an `edit_key` and mapped to one or more selected repair targets.
_Avoid_: VoxBlame recommendation

**Region Diff**:
Objective before/after surface-occupancy evidence over the selected targets' fixed masks, their halos, and the remaining space.
_Avoid_: Verdict, rollback decision

**Exterior Surface**:
Candidate surface lying beyond the canonical cube by more than the canonical epsilon. Its existence always vetoes acceptance.
_Avoid_: Outlier, harmless overflow

**Observable Geometry**:
The combined identity of interior surface occupancy, exterior surface occupancy, and exterior resolution metadata.
_Avoid_: Mesh file hash, exact BRep identity

**Accepted Step**:
A measured step whose depth-8 interior surface sets match exactly and whose exterior surface is clear.
_Avoid_: Best step, visually good step

**Selected Step**:
The measured step chosen for final delivery after comparing the plausible candidates. It may remain unaccepted.
_Avoid_: Latest step, accepted step

**Final Delivery**:
The atomically published package rebuilt from the selected step's source and verified to reproduce its observable geometry.
_Avoid_: Copy of the latest files

**Workspace**:
The experiment-local state graph containing immutable measured steps, repair cycles, attempts, VoxBlame evidence, and final delivery, plus an ignored mutable work area.
_Avoid_: Output folder, VoxBlame directory

**Workspace Authority**:
The immutable reconstruction facts whose publication changes a Workspace state graph. Runner completion, transfer results, review verdicts, and evaluation scores are outside this authority.
_Avoid_: Experiment contents, output manifest, review authority

**Workspace View**:
A validated, read-only projection of Workspace Authority facts for a declared consumer purpose.
_Avoid_: Workspace copy, report, cached directory scan

**Agent Surface**:
The closed set of reconstruction intents and purpose-bound Workspace facts available to a Modeling Agent without exposing Workspace Authority or its publication machinery.
_Avoid_: Internal, Workspace interface, authority CLI

**Agent Intent**:
A domain-level request made through the Agent Surface to observe a reference, develop or measure a candidate, submit a repair result, select a step, or request delivery.
_Avoid_: Authority publication, shell command, file operation

**Consumer Verdict**:
A conclusion produced by a runner, reviewer, evaluator, or transfer process from a Workspace View without becoming Workspace Authority.
_Avoid_: Workspace fact, acceptance fact, transaction result

**Reference Capability**:
A job-private authority to request a closed set of bounded observations of one Canonical Reference without exposing its raw representation.
_Avoid_: Mesh path, read-only mesh, geometry export

**Reference Observation**:
A bounded fact or rendering returned through a Reference Capability for reconstruction reasoning.
_Avoid_: Raw mesh, Workspace inspection, arbitrary geometry query

**Terminal Validation Result**:
The reusable result of the required complete Workspace validation after pilot execution reaches a terminal state.
_Avoid_: Final Delivery, review verdict, repeated review validation

**Reference Binding**:
The trusted association, established during outer Workspace preparation and initialization, between a Reference Capability instance and the single Canonical Reference committed by one Workspace. It names one absolute Canonical Reference location and the published content and identity digests that describe it. It is derived from Workspace state, never chosen by the Agent, never overridden by ambient environment, and proven before the Agent Surface starts.
_Avoid_: Reference path option, environment override, Agent-selected reference, foreign identity claim

**Trusted Candidate Execution**:
The choreography of Agent Intents through which fixed, release-shipped tools develop, measure, preview, and diff one candidate under Workspace Authority. All evidence flows through this choreography; its packaging is not a runtime cache, lease, or separate domain object.
_Avoid_: Agent build command, Agent-owned measurement, side-channel preview, runtime tool bundle

**Terminal Validation Handoff**:
The runner-owned, one-way transfer of a Terminal Validation Result and expected identity to a downstream consumer over its own trust lineage. Its storage is invisible to the Agent and uses the host's supported lock and atomic-file primitives; unsupported hosts fail before publication. It is independent of Workspace Authority and does not introduce a general transaction or receipt framework.
_Avoid_: Signed evidence bundle, KMS-brokered receipt, review authority publication, cross-platform transaction framework

**Agent Source Projection**:
The five-file, Agent-only subset of installed skill source materialized by bundle and release with an exact manifest. The runner verifies and mounts that subset; it does not regenerate it or mount a complete installed skill tree.
_Avoid_: Full skill mount, workspace source bind, installed plugin tree, runtime projection builder

## Browser Runtime Language

**Local Rendering Browser**:
A job-scoped browser environment for repository-owned local rendering programs. It may render the interactive CAD Viewer or a formal residual preview, but it cannot navigate arbitrary external webpages.
_Avoid_: Web agent, general-purpose browser, remote browser provider

**Browser Runtime**:
The sole job-scoped browser execution environment started and terminated by the outer pilot runner. It exposes a bounded MCP connection and fixed registered render operations from one exact OCI image. The Agent never owns Chromium, installs a browser, selects an image tag, or falls back to a local browser.
_Avoid_: Browser Sidecar, Agent browser process, shared browser pool, fallback browser

**Browser Runtime Artifact**:
The immutable linux/amd64 OCI image selected only by exact `sha256` image ID for Provider-Free Browser Execution. It contains the version-matched Playwright MCP server, Chromium, and approved Render Program builds, and records the exact source revision in its OCI metadata.
_Avoid_: Sidecar Artifact, image tag, browser cache, mutable container

**Render Program**:
A versioned, pre-registered repository-owned rendering workload baked into the Browser Runtime Artifact, such as the formal eight-view residual renderer. The Agent supplies closed structured inputs; it cannot submit arbitrary HTML, JavaScript, URLs, paths, browser arguments, or runtime options.
_Avoid_: Arbitrary webpage, arbitrary script, browser task

**Self-Contained GLB**:
A single GLB whose complete scene, buffers, images, and other model resources are embedded in that file, so loading it never resolves an external model resource.
_Avoid_: GLB package, model URL, GLB with side files

**Browser Runtime Capability**:
The job-private, read-only capability that binds one Agent execution to the exact Browser Runtime image ID, job ID, loopback endpoints, bearer token, and registered Render Program digests. Missing, replaceable, malformed, stale, or mismatched capabilities fail closed.
_Avoid_: Browser Authority, broker authority, socket discovery, environment-selected endpoint

**CVM Development Proof**:
Behavioral evidence produced on CVM without claiming formal artifact identity, Source-Hidden execution, or pilot acceptance.
_Avoid_: Formal CVM proof, provision receipt, accepted pilot

**Provider-Free Browser Execution**:
Browser execution using a pre-provisioned, digest-pinned local artifact and job-private connection, with no runtime browser-provider call or browser download. A preloaded image on Colima or CVM remains Provider-Free.
_Avoid_: Rebuilding Chromium per job, managed cloud browser, runtime browser install

**Browser Execution Tree**:
The read-only Browser Runtime Artifact filesystem visible to Chromium, containing only approved browser and Render Program assets and no source workspace mount.
_Avoid_: Browser cache, staged browser, source tree, source bind mount

**Source-Hidden**:
A boundary where the preview renderer cannot resolve any supervisor-owned source or materialization alias, including through another process's root view. Read-only access alone is not Source-Hidden.
_Avoid_: Read-only, chmod-protected, direct path hidden

**Cleanup Diagnostic**:
A closed, authority-bound receipt naming the exact cleanup owner and failed predicate without exposing raw process, path, mount, or error details.
_Avoid_: Error string, cleanup log, exception detail

**Retained Resource Proof**:
Positive closed evidence that an owned process, mount, profile, socket, or private tree remains after cleanup. It supersedes an earlier transition failure because it proves terminal residue.
_Avoid_: Cleanup error, failed emptiness check

## Agent Runtime Language

**Agent Runtime Artifact**:
The browser-free, linux/amd64 OCI image selected by digest for one formal Agent execution environment. It contains the fixed Codex, Python, CAD, shell-tool, certificate, and native-library runtime, but no project source, job input, credential, Browser Sidecar, or Docker authority.
_Avoid_: CVM environment, host runtime, Agent filesystem, copied virtualenv

**Source Snapshot**:
The immutable, digest-identified project source mounted read-only into an Agent Runtime Artifact for one execution. Its identity is independent of the Agent Runtime Artifact so source revisions do not require rebuilding the runtime.
_Avoid_: Agent image, current checkout, source bind mount

**Agent Runtime Verified**:
The state reached when an exact Agent Runtime Artifact and its declared runtime manifest pass browser-deny, isolation, CAD, Codex, Colima, and provider-free CVM conformance with published receipts.
_Avoid_: Image built, local smoke passed, Formal Pilot Integrated

**Formal Pilot Integrated**:
The state reached when an Agent Runtime Verified artifact and an exact Source Snapshot complete the authorized real formal pilot and publish its terminal evidence.
_Avoid_: Agent Runtime Verified, provider-free pass, image ready

**Cup Runtime Capability Manifest**:
The versioned allowlist of commands, Python imports, native libraries, CAD fixtures, network capabilities, and Render Programs promised by the first Agent Runtime Artifact for the formal Cup pilot. The same manifest is exercised by Colima and provider-free CVM conformance.
_Avoid_: Complete CAD environment, installed-package list, smoke-test notes

**Broker-Backed Preview**:
A formal preview performed through an approved Render Program reached through a job-private browser capability. Direct Playwright, Chromium, or local browser startup by the Agent is outside this boundary.
_Avoid_: CAD snapshot, local browser, Agent browser

**Agent Execution**:
One job-private container execution of an exact Agent Runtime Artifact and Source Snapshot, with its own inputs, outputs, Codex home, capabilities, lifecycle, and receipts. Concurrent Agent Executions may share immutable image layers but never Broker, Sidecar, writable state, or job authority.
_Avoid_: Agent image copy, shared Agent, experiment process

**Agent Concurrency Contract**:
The proven number of simultaneous Agent Executions whose job-private capabilities, outputs, cleanup, and receipts remain isolated. It is distinct from queue capacity and does not promise unbounded simultaneous execution.
_Avoid_: Batch size, experiment count, unlimited concurrency
