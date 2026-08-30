---
name: mesh-to-cad
description: Reconstruct a mesh through the bounded canonical repair Workspace and publish a rebuilt, verified Final Delivery.
---

# Mesh-to-CAD canonical repair Workspace

## Purpose

Reconstruct a raw mesh through one auditable protocol:

```text
Canonical Reference
  → Measured Step 0
  → zero to ten Repair Cycles
  → Selected Step
  → isolated rebuild and Observable Geometry verification
  → Final Delivery
```

The Workspace is the authority. Its Measured Steps, Repair Cycles, Attempts,
Repair Batches, previews, measurements, selection, and Final Delivery are
immutable evidence. Mutable modeling files live only under `work/`.

## Ownership

- `$mesh-inspect` supplies geometric evidence for the modeling brief.
- `$cad` owns source authoring and the registered canonical build/rebuild
  recipe.
- `$mesh-compare` owns Canonical Reference preparation, Measured Step facts,
  Repair Targets, Region Diff, previews, and rebuild verification.
- `scripts/mesh-to-cad-workspace` owns publication, budgets, ancestry,
  protocol-scoped Git/LFS commits, recovery, selection, and Final Delivery.
- `scripts/mesh-to-cad-agent-surface` owns the closed Agent intent seam and
  delegates through supervisor-injected opaque handles; it does not discover
  Workspace or reference paths.
- The Agent owns Repair Batch selection, Planned Edits, assessments, stop
  reasons, and the Selected Step.

Read `references/workspace-contract.md` for helper commands and transaction
semantics. Read `references/output-schemas.md` before authoring any setup,
plan, assessment, selection, or notes document.

## Reconstruction Spec (enabled by default)

The Reconstruction Spec is default-on (enabled by default). A task or pilot
instruction may explicitly opt out for a controlled execution. Do not add a
CLI mode, experiment field, or automatic detection. Read
`references/reconstruction-spec.md` for its small JSON contract.

When enabled (the default unless the task or pilot explicitly opts out), the
Agent creates `<EXP_DIR>/run/reconstruction-spec.json` only after raw-input
inspection and Canonical Reference preparation, and before the first CAD
authoring or Step 0. Read it before initial modeling; before each Repair
Hypothesis, read it again. If the geometric understanding changes, update the
same file in place. Use raw-mesh inspection and Canonical Reference geometry
as evidence, and ignore user-provided category, function, or part semantic
hints.

This is a mutable working document, not a CAD or source plan. It stays in the
`run/` non-authority area and never enters Workspace `setup/`, `steps/`,
`cycles/`, or final authority/Final Delivery. It does not change cadgen/runtime
or the formal Workspace schema state machine. Repair Targets, source callables,
AssemblyHelper objects, and STEP labels do not automatically become Spec items.
Do not add a Spec field to Repair Batch or other Workspace output schemas; cite
Spec IDs in prose hypotheses, plans, or notes when useful.

## Required workflow

### 1. Prepare and initialize

1. Inspect the raw input with `$mesh-inspect` and write CAD modeling evidence
   under `<PREPARED>/setup/`. Inputs that cannot be represented honestly as
   STEP-first parametric CAD must stop with a declared modeling limitation.
2. Run `$mesh-compare voxblame-prepare-reference` into `<PREPARED>/input`.
   This is the experiment's only normalization.
3. Freeze `<PREPARED>/experiment.json` with Workspace ID, canonical-reference
   identity, coordinate contract, and residual-preview profile.
4. Ensure `<EXP_DIR>` is a Git repository root with no pre-staged paths, then
   initialize:

```bash
python skills/mesh-to-cad/scripts/mesh-to-cad-workspace init \
  --workspace <EXP_DIR> --prepared <PREPARED>
```

Do not write authority files directly after initialization.

5. If Reconstruction Spec is enabled (the default unless the task or pilot
   explicitly opts out), create `<EXP_DIR>/run/reconstruction-spec.json` now.
   It must remain mutable and outside Workspace authority; do not pass it to a
   Workspace helper.

### Attempt command recording

After `begin-attempt`, invoke canonical CAD through
`mesh-to-cad-workspace build`; invoke every other fallible preview,
measurement, and diff command through `mesh-to-cad-workspace run` with an
explicit phase. A nonzero command completes the Attempt only after its command document is
published and `record-attempt` records `result=tool_failure` with the tool's
reported classification. In particular, a `preview_failed` result is a tool
failure named `preview_failed`; it is not evidence of a representation limit
or of no feasible CAD strategy.

### 2. Publish Measured Step 0

1. If Reconstruction Spec is enabled, read
   `<EXP_DIR>/run/reconstruction-spec.json` before initial CAD authoring.
2. Author `mesh-to-cad.initial-plan/1` and begin Attempt 0:

```bash
python skills/mesh-to-cad/scripts/mesh-to-cad-workspace begin-attempt \
  --workspace <EXP_DIR> --plan <initial-plan.json> --intended-step 0
```

3. Run each build operation through the bounded, registered `build` command.
   `$cad` must build directly in canonical coordinates and leave a complete source
   bundle, registered offline rebuild recipe, CAD artifacts, and measurement
   GLB under the Attempt's candidate directory. From Step 0 onward, keep the
   rebuild recipe bundle-relative and ready for isolated finalization. Give
   every rebuild a new empty output directory.

   Pass `--source`, every `--input`, and `--output-dir` as
   experiment-root-relative paths. The Workspace reads the absolute adapter
   and digest from the trusted registry; do not supply or reconstruct a
   launcher. Canonical source execution
   uses the candidate bundle as cwd, so a generator reads each sidecar through
   its bundle-relative path (for example `Path("source/width.txt")`); that same
   file is passed to the initial build through experiment-root-relative
   `--input`. The output directory must be new. `build` treats the exact
   provider-free invocation as provisional until it produces both `build.json`
   and `measurement.glb`; a preflight failure is cleaned and spends no command
   from the active Attempt. On success, that invocation is recorded as the
   formal build command:

```bash
python skills/mesh-to-cad/scripts/mesh-to-cad-workspace build \
  --workspace <EXP_DIR> --attempt <A> \
  --source work/attempts/<A>/candidate/source/model.py \
  --input work/attempts/<A>/candidate/source/width.txt \
  --output-dir work/attempts/<A>/candidate/artifacts \
  --tool-registry /run/meshshot-browser/trusted-tool-registry.json
```

4. Run `$mesh-compare voxblame-preview` on the candidate and inspect all eight
   views.
5. Run `$mesh-compare voxblame-measure` with `--step 0` and no parent.
6. Publish only after candidate, preview, measurement, Canonical Reference,
   and profile identities agree:

```bash
python skills/mesh-to-cad/scripts/mesh-to-cad-workspace publish-step-zero \
  --workspace <EXP_DIR> --attempt <A> --candidate <candidate-dir> \
  --candidate-mesh <candidate-relative-glb> \
  --measurement <measurement-dir> --preview <preview-dir>
```

### 3. Run bounded Repair Cycles

For the chosen parent Measured Step:

1. Read the Repair Frontier's Active Repair Depth and exterior alerts, then
   page every current directional Repair Target in attention order. `missing`
   requires adding geometry, `excess` requires removing or shrinking geometry,
   and `exterior` requires recovering geometry outside the canonical frame.
   Inspect its bounds, objective missing/excess evidence, and formal preview. Do not choose
   or advance repair depth manually.
2. If Reconstruction Spec is enabled, reread
   `<EXP_DIR>/run/reconstruction-spec.json` immediately before forming each
   Repair Hypothesis. If the current geometric understanding changes, update
   the Spec in place. Form one or more falsifiable Repair Hypotheses from the
   current Repair Frontier, formal preview, CAD source, and repair history.
   Decide whether one coherent repair is plausible. If so, select one
   hypothesis and author one `voxblame.repair-batch/1` selecting one or more
   Repair Targets and mapping stable Planned Edit keys to them. The Repair
   Batch and other output schemas do not gain Spec fields; cite Spec IDs in
   prose when useful.
3. Begin an Attempt with an explicit `--from-step <M>` and a new intended step.
4. Execute the Planned Edits through bounded `run` calls, rebuild into a new
   empty output directory, render the child preview, and measure the child
   with explicit `--compare-to <M>`.
5. Run `$mesh-compare voxblame-diff` for the frozen parent/child edge. Inspect
   exact-mask, halo, outside-selected, trajectory, and Exterior Surface facts.
6. Write Agent assessment and source-change evidence, then atomically publish
   the Measured Step and Repair Cycle:

```bash
python skills/mesh-to-cad/scripts/mesh-to-cad-workspace publish-cycle \
  --workspace <EXP_DIR> --attempt <A> --candidate <candidate-dir> \
  --candidate-mesh <candidate-relative-glb> \
  --measurement <measurement-dir> --preview <preview-dir> \
  --region-diff <region-diff.json> --assessment <assessment.json> \
  --source-changes <source-changes.json>
```

An Attempt that cannot publish a Measured Step must be frozen with
`record-attempt`. A failed Attempt consumes no Repair Cycle. A successfully
measured geometric no-op consumes one. Use Repair Cycles for falsifiable,
geometry-directed edits; make recipe and provenance corrections before a
Measured Step or while preparing finalization. The Workspace permits at most
ten successful Repair Cycles, three Attempts per intended step, and two actual
tool failures per intended step.

Stop repair immediately when the objective facts establish acceptance. The
Agent may stop earlier when no feasible coherent repair remains. Branching from
any earlier Measured Step is allowed; numeric adjacency never implies ancestry.

### 4. Select and finalize

1. Compare the strongest plausible Measured Steps, including any plausible
   superior competitor. Inspect their previews and objective evidence.
2. Author `mesh-to-cad.final-selection/1`. The Selected Step may be unaccepted;
   record the honest stop reason and retain its acceptance state unchanged.
3. Write `notes.md` with the seven fixed domain-glossary sections.
4. Finalize with orchestrator-supplied trusted entrypoints and tool registry:

```bash
python skills/mesh-to-cad/scripts/mesh-to-cad-workspace finalize \
  --workspace <EXP_DIR> --selection <final-selection.json> \
  --notes <notes.md> --rebuild-entrypoint <registered-cad-adapter> \
  --geometry-entrypoint <mesh-compare-entrypoint> \
  --tool-registry /run/meshshot-browser/trusted-tool-registry.json
```

The formal pilot runner supplies that registry on the same read-only authority
mount as the Browser Runtime capability. Never create, copy, hash, or repair a
tool registry under the experiment. Outside the formal pilot environment, the
calling orchestrator must supply an equivalent read-only registry explicitly.

Finalization copies the Selected Step source into isolated staging, performs
the registered offline rebuild without source edits, validates build
provenance, verifies Observable Geometry against the Selected Step, renders the
final preview, and atomically publishes Final Delivery. It never upgrades an
unaccepted selection and never substitutes a historical artifact.

5. Run `validate`. If interrupted, run `recover` and validate again; do not
   repair authority files by hand.

## Handoff

Return:

- `notes.md` and `step_index.json`;
- Selected Step number, inherited acceptance, stop reason, and considered
  competitors;
- `final/source/`, `final/artifacts/`, `final/build.json`, and
  `final/rebuild.json`;
- unchanged `final/measurement.json` and separate
  `final/verification.json`;
- `final/preview.png`, `final/preview.json`, `final/selection.json`, and
  `final/manifest.json`;
- the final `validate` result and protocol-scoped Git commit.

If Final Delivery was not published, report the exact classification and the
last valid Measured Step/Attempt. Never claim acceptance, rebuild provenance,
or verification without the corresponding authority artifact.

## Non-negotiables

- One Canonical Reference; no candidate alignment or independent normalization.
- Use only the public Workspace helper for authority publication and Git/LFS.
- Every nonzero Measured Step, Repair Cycle, Attempt, Region Diff, and
  assessment names the same explicit parent.
- VoxBlame facts never prescribe CAD changes or decide when the Agent stops.
- Keep runner telemetry outside Workspace authority.
- Final Delivery is rebuilt from archived Selected Step source and verified;
  historical outputs are not substitutes.
- Report only checks and artifacts actually observed.

## Progressive references

- `references/reconstruction-spec.md`: default-on, mutable reference-semantics
  working document; task or pilot executions may explicitly opt out.
- `references/output-schemas.md`: Agent-authored setup and evidence documents.
- `references/workspace-contract.md`: helper commands, bounds, recovery, Git,
  LFS, and Final Delivery publication.
