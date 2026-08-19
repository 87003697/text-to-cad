# Agent-authored Workspace documents

Read this reference before creating setup, plan, assessment, selection, or
notes inputs for `mesh-to-cad-workspace`. All JSON objects are closed: do not
add fields.

## Prepared setup

`<PREPARED>/experiment.json`:

```json
{
  "schema": "mesh-to-cad.experiment/1",
  "workspace_id": "stable-lowercase-id",
  "coordinate_contract": "trellis2_canonical/1",
  "canonical_reference_sha256": "<sha256>",
  "preview_profile": {
    "name": "cadena_residual_eight_view/1",
    "sha256": "<sha256>"
  }
}
```

If STEP-first CAD cannot express the input honestly, record that limitation in
the plan and stop reason.

## Initial plan

```json
{
  "schema": "mesh-to-cad.initial-plan/1",
  "summary": "Build the first CAD candidate directly in canonical coordinates."
}
```

## Repair Batch

```json
{
  "schema": "voxblame.repair-batch/1",
  "from_step": 2,
  "selected_targets": [
    {"target_key": "target-key", "mask_sha256": "<sha256>"}
  ],
  "planned_edits": [
    {
      "edit_key": "edit-key",
      "target_keys": ["target-key"],
      "description": "Agent-authored modeling change"
    }
  ],
  "rationale": "Why these targets form one coherent modeling problem.",
  "preview_observation": "What the formal preview shows before editing."
}
```

Select at least one target and write at least one Planned Edit. Every selected
target must be mapped by an edit. Keys are stable lowercase identifiers. The
plan records intent; it does not copy objective measurement fields.

## Cycle assessment

```json
{
  "schema": "mesh-to-cad.assessment/1",
  "from_step": 2,
  "to_step": 4,
  "preview_observation": "Observed child preview result.",
  "summary": "Agent assessment grounded in Region Diff evidence."
}
```

`source-changes.json`:

```json
{
  "schema": "mesh-to-cad.source-changes/1",
  "from_step": 2,
  "to_step": 4,
  "files": [
    {
      "path": "source/model.py",
      "before_sha256": "<sha256>",
      "after_sha256": "<sha256>"
    }
  ]
}
```

The parent/child pair must match the Measured Step, Repair Cycle, Attempt, and
Region Diff edge.

## Final selection

```json
{
  "schema": "mesh-to-cad.final-selection/1",
  "considered_steps": [0, 2, 4],
  "selected_step": 4,
  "preview": {
    "identity_sha256": "<selected-preview-identity>",
    "observation": "Agent inspection of the Selected Step preview.",
    "evidence_conflict": false,
    "conflict_details": null
  },
  "accepted": false,
  "stop_reason": "cycle_limit",
  "evidence": [
    {"kind": "measurement", "path": "steps/000004/measurement/summary.json", "sha256": "<sha256>"}
  ]
}
```

`considered_steps` names every plausible superior competitor that was reviewed.
`selected_step` must be in that set. `accepted` must equal the Selected Step's
stored acceptance. Supported stop reasons are:

- `acceptance_satisfied`
- `cycle_limit`
- `no_feasible_strategy`
- `representation_limit`
- `modeling_intent_conflict`
- `repeated_ineffective_strategy`
- `tool_failure`

An accepted selection uses `acceptance_satisfied`; an unaccepted selection
cannot. Any automatic identity conflict or Agent-reported material semantic
conflict blocks Final Delivery.

## Final notes

`notes.md` contains exactly these headings in this order:

1. `## Input`
2. `## Modeling Intent`
3. `## Preserved Structural Features`
4. `## Omitted Surface Details`
5. `## Repair Trajectory`
6. `## Final Selection`
7. `## Verification`

Record the Canonical Reference, intended structure, declared
omissions, Measured Step/Repair Cycle ancestry, considered and Selected Steps,
inherited acceptance, stop reason, rebuild provenance, Observable Geometry
verification, final preview, and primary artifacts. Cite paths; do not invent
facts that are absent from Workspace authority.
