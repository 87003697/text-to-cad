# Text-to-CAD Reconstruction and Execution

This context defines the language used by reconstruction evaluation, provider-free preview execution, and the sealed Agent runtime. It names domain concepts only; ADRs and design documents own implementation decisions.

## Reconstruction Language

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

## Browser Runtime Language

**Local Rendering Browser**:
A job-scoped browser environment for repository-owned local rendering programs. It may render the interactive CAD Viewer or a formal residual preview, but it cannot navigate arbitrary external webpages.
_Avoid_: Web agent, general-purpose browser, remote browser provider

**Browser Sidecar**:
The job-scoped Local Rendering Browser execution environment started and terminated by the outer job authority. One sidecar may serve multiple renders within its job, but each render receives a fresh browser context. The Agent receives only a bounded local browser connection and never owns the browser executable or process lifecycle.
_Avoid_: Agent browser process, shared browser pool, nested Chromium

**Sidecar Artifact**:
The platform-specific, immutable OCI image selected by digest for Provider-Free Browser Execution. It contains the version-matched Playwright Server, Chromium, and approved Render Program builds.
_Avoid_: Image tag, browser cache, runtime build, mutable container

**Render Program**:
A versioned, pre-registered repository-owned rendering workload accepted by the Browser Sidecar, such as the CAD Viewer or the formal eight-view residual renderer. The Agent supplies structured model, camera, and rendering inputs; it cannot submit arbitrary HTML or JavaScript.
_Avoid_: Arbitrary webpage, arbitrary script, browser task

**Self-Contained GLB**:
A single GLB whose complete scene, buffers, images, and other model resources are embedded in that file, so loading it never resolves an external model resource.
_Avoid_: GLB package, model URL, GLB with side files

**Viewer Integration Proof**:
CVM development evidence that the Browser Sidecar loaded the designated Self-Contained GLB through the current CAD Viewer, captured its initial orthographic view, selected perspective through the existing Viewer UI, captured the changed view, and observed no Viewer error.
_Avoid_: Viewer page opened, HTTP success, canvas present, screenshot smoke

**Viewer Projection Trace**:
The replayable two-state evidence sequence `Orthographic (initial) → Perspective (explicit selection)`, with the observed projection state and viewport capture at both states.
_Avoid_: Toggle test, click trace, final screenshot

**Sidecar Asset Backend**:
The request-scoped provider that presents model data to the real CAD Viewer through the Viewer’s catalog and asset contract; it does not own Chromium or Viewer interaction semantics.
_Avoid_: Sidecar Viewer, browser server, model injector

**Viewer Control Run**:
A CVM development run in which the current workspace Viewer server presents a durable Self-Contained GLB to the Browser Sidecar, establishing the expected loading and interaction behavior before the Sidecar Asset Backend is introduced.
_Avoid_: Production baseline, upstream conformance, formal pilot

**Asset Backend Run**:
A CVM development run that repeats a Viewer Control Run’s model and interactions while replacing only the model provider with the Sidecar Asset Backend.
_Avoid_: Second implementation, formal pilot, different test

**CVM Development Proof**:
Behavioral evidence produced on CVM without claiming formal artifact identity, Source-Hidden execution, or pilot acceptance.
_Avoid_: Formal CVM proof, provision receipt, accepted pilot

**Provider-Free Browser Execution**:
Browser execution using a pre-provisioned, digest-pinned local artifact and job-private connection, with no runtime browser-provider call or browser download. A preloaded image on Colima or CVM remains Provider-Free.
_Avoid_: Rebuilding Chromium per job, managed cloud browser, runtime browser install

**Browser Authority**:
The experiment-bound identity and outer lifecycle authority for the exact Sidecar Artifact and Render Program used to produce a local render.
_Avoid_: Browser path, Chromium process, pinned executable

**Browser Execution Tree**:
The read-only Sidecar Artifact filesystem visible to Chromium, containing only approved browser and Render Program assets and no source workspace mount.
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
