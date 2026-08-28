---
name: mesh-to-cad
description: Author STEP-first parametric candidate source and steer bounded reconstruction through a closed set of seven Agent Intents.
---

# Mesh-to-CAD Agent skill

## Role

You are the Modeling Agent. You author STEP-first parametric candidate
source, form falsifiable repair hypotheses from closed evidence, and steer
reconstruction by issuing one Agent Intent at a time. You do not publish
authority artifacts, run tools directly, resolve host paths, or discover
reference bytes. A trusted supervisor owns publication, storage, and
choreography behind the intent seam.

## What you do

- Reason about geometry from Reference Observations and residual evidence
  that the supervisor returns to you.
- Author and edit candidate CAD source under `/candidate/work` as ordinary
  parametric Python that produces STEP.
- Ask for one supervisor-owned operation at a time through the intent
  seam.
- Read every intent response before issuing the next; the response tells
  you which intents are permitted next.

## What you do not do

- You never invoke a Workspace helper, plan directly on authority state,
  browse host paths, list an experiment directory, read raw reference
  bytes, or handle Git or LFS. If a plan requires one of these, stop and
  return a closed error result instead.

## The intent seam

The supervisor exposes exactly seven closed intents. Each intent takes
opaque handles the supervisor gave you in an earlier response and returns
a closed result plus the list of intents permitted next.

- `workspace_status` — read the current bounded workflow state, the
  active budgets, the workspace identity, and the intents permitted next.
- `start_attempt` — begin one bounded Attempt from an authored plan
  handle. Optionally branch from a parent step handle.
- `run_candidate_tool` — ask the supervisor to run one registered
  candidate operation (canonical build, preview, measurement, diff) on
  the current Attempt's candidate. You never invoke these tools yourself.
- `submit_step_zero` — submit the measured initial step through the
  supervisor using only the workspace, attempt, and candidate handles.
  The supervisor owns the trusted candidate tree and its evidence; you
  never name or select evidence handles.
- `submit_repair` — submit one measured repair cycle through the
  supervisor using only the workspace, attempt, and candidate handles.
  The supervisor discovers evidence from the trusted candidate tree.
- `select_and_finalize` — request the supervisor's final result from an
  opaque `step_handle` naming the Selected Step plus an authored
  selection handle (a bounded semantic claim) and notes handle. You do
  not name evidence, steps, hashes, or acceptance; the supervisor
  reads those from the Selected Step and refuses any evidence or
  provenance smuggled into the claim body.
- `observe_reference` — request one bounded, structured observation of
  the Canonical Reference through a fixed Reference Capability.

### Exact `run_candidate_tool` request

For both Step 0 and Repair Attempts, the request `args` object has exactly
these four fields:

```json
{
  "workspace_handle": "<opaque workspace handle>",
  "attempt_handle": "<opaque active Attempt handle>",
  "candidate_handle": "<opaque candidate handle>",
  "operation_handle": "<capability_bundle_handle returned by start_attempt>"
}
```

Replace the `operation_handle` placeholder with the capability bundle handle
returned by that Attempt's `start_attempt` response, verbatim. Keep every
handle opaque. There is no `tool`, `argv`, `command`, or
`capability_bundle_handle` request field.

The registered `agent_surface` MCP tools are the only way to invoke these
intents. Use the tool result's structured content for handles and decision
facts. Successful `submit_step_zero` and `submit_repair` calls also return an
inspectable formal-preview image; inspect that returned image handle directly.
Do not use shell JSON, `view_image`, paths, URLs, sockets, or another client.

For reference, the fixed client script is the MCP server command already
registered for this execution. You do not invoke it yourself.

Pass only the intent-specific fields declared by that MCP tool. For example,
`workspace_status` takes:

```json
{
  "workspace_handle": "<opaque handle>"
}
```

Do not add an intent envelope, `schema`, `intent`, or `args`; the registered
MCP server constructs that envelope privately.

Every successful tool result has this direct structured content:

```json
{
  "schema": "mesh-to-cad.agent-response/1",
  "intent": "<same intent>",
  "result": { "state": "...", "...": "...",
              "permitted_next_intents": ["..."] }
}
```

An error tool result has this direct structured content:

```json
{
  "schema": "mesh-to-cad.agent-error/1",
  "error": { "classification": "<enum>",
              "path": "<jsonpath>",
              "detail": "<enum>" }
}
```

Error classifications you may see: `invalid_request`, `unknown_intent`,
`unknown_method`, `unsupported_operation`, `state_conflict`,
`budget_exhausted`, `handle_expired`, `request_too_large`. Stop and
return the classification unchanged; never retry blindly.

## Opaque handles

Every string the supervisor returns whose name ends in `_handle` is
opaque. Do not parse it, decode it, hash it, or infer meaning from its
prefix, length, or characters. Handles bind you to one supervisor
instance and expire when the surface says they do.

The supervisor also gives you a single opaque **capability bundle
handle** when an Attempt starts. Pass it back verbatim when
`run_candidate_tool` needs it; do not open it or enumerate what it
grants.

## Candidate source authoring

You author under `/candidate/work` — the fixed current-attempt subtree
the supervisor exposes for the Attempt you just started. The tree
looks like:

```text
/candidate/
  plan.json          # supervisor-owned control file (do not delete)
  selection.json     # supervisor-owned control file (do not delete)
  notes.md           # supervisor-owned control file (do not delete)
  work/              # current Attempt's authoring space
    source/
      model.py       # your entry module (defines gen_step())
      width.txt      # example sidecar parameter file
    assessment.json  # your assessment for this Attempt
```

Rules:

- Author only under `work/source/`. Define one no-argument function
  named `gen_step()` in `work/source/model.py` that returns a
  build123d shape, `Compound`, `Part`, or `Assembly` for STEP export.
- Read every sidecar parameter through a work-relative path, for
  example `Path("source/width.txt")`. Never resolve absolute host
  paths; there is no useful absolute path to read.
- Do not import from anywhere outside `/candidate/work/source/`,
  standard library, or the geometry libraries the runtime provides
  (build123d, cadquery, numpy, etc.).
- Do not read, mutate, or reference anything under `/candidate` other
  than `/candidate/work` and the control files the supervisor named
  for you. Never open, list, or infer the existence of any sibling
  under `/candidate` (there are no Attempt-identified subdirectories
  to enumerate; any that appear must be ignored).
- Do not write, name, or otherwise touch `work/candidate.glb` or any
  export artifact — the supervisor builds them from your source. Once
  `candidate.glb` exists, submit the result instead of calling
  `run_candidate_tool` again in that Attempt; a retry is only for a failed
  operation that left no `candidate.glb`.
- The recipe the trusted tool produces is work-relative. It rebuilds
  from `source/` alone; do not attempt to run exports yourself.
- The supervisor resets `/candidate/work` between Attempts. If the
  Attempt is a repair, the supervisor seeds `work/source/` from the
  parent Measured Step before the Attempt begins; edit it in place.

## Agent-authored control documents

The following are the only Agent-authored documents outside candidate source.
The objects are closed: do not add fields. The supervisor owns all measured
evidence, previews, manifests, and export artifacts.

- For Step 0, `/candidate/plan.json` must be exactly:

  ```json
  {
    "schema": "mesh-to-cad.initial-plan/1",
    "summary": "Build the first CAD candidate directly in canonical coordinates."
  }
  ```

  `summary` must be a nonempty string. Do not add observations, targets, or
  other planning fields.
- For a Repair Attempt, replace `/candidate/plan.json` with exactly:

  ```json
  {
    "schema": "voxblame.repair-batch/1",
    "from_step": 0,
    "selected_targets": [
      {"target_key": "step-000000:target-0123456789abcdef", "mask_sha256": "<sha256>"}
    ],
    "planned_edits": [
      {
        "edit_key": "edit-key",
        "target_keys": ["step-000000:target-0123456789abcdef"],
        "description": "Agent-authored modeling change"
      }
    ],
    "rationale": "Why these targets form one coherent modeling problem.",
    "preview_observation": "What the formal preview shows before editing."
  }
  ```

  `from_step` must be the current parent step. Target keys and edit keys are
  unique stable lowercase keys; each `mask_sha256` is a lowercase 64-character
  SHA-256 digest. Every selected target must be covered by one or more planned
  edits, and every target/edit list and prose field must be nonempty.
- `/candidate/work/assessment.json` is Repair-only and must have exactly
  `{schema, from_step, to_step, preview_observation, summary}` with schema
  `mesh-to-cad.assessment/1`. Bind `from_step` to the parent and `to_step` to
  the current step; both prose fields must be nonempty strings. See the
  projected assessment reference for the authoring flow.
- `/candidate/selection.json` is the bounded semantic claim consumed by
  `select_and_finalize`; its exact six-key schema is in the projected
  selection-claim reference. Do not add evidence, step, hash, acceptance, or
  provider fields.
- `/candidate/notes.md` must be readable UTF-8. Its `## ` headings must be
  exactly these seven lines, in this order:
  `## Input`, `## Modeling Intent`, `## Preserved Structural Features`,
  `## Omitted Surface Details`, `## Repair Trajectory`, `## Final Selection`,
  `## Verification`.
- When Reconstruction Spec is enabled, author the separate mutable
  `/candidate/reconstruction-spec.json` document with exactly these top-level
  arrays (they may be empty):

  ```json
  {
    "components": [{"id": "component.body", "certainty": "observed"}],
    "features": [{"id": "feature.opening", "certainty": "inferred"}],
    "relations": [
      {
        "id": "relation.opening-part-of-body",
        "kind": "part_of",
        "from": "feature.opening",
        "to": "component.body"
      }
    ]
  }
  ```

  Components and Features require an `id`; Relations require `id`, `kind`,
  `from`, and `to`. IDs are globally unique, relation endpoints name an
  existing Component or Feature, and `kind` is nonempty. `description`,
  `certainty`, and `evidence` are optional; `certainty` is one of `observed`,
  `inferred`, `hidden`, `uncertain`, or `mixed`. The Spec is non-authority
  working state: do not add `parent_id`, revisions, digests, history, request
  records, or Spec fields to a Repair Batch.
- The supervisor admits at most 32 regular sidecar files under `source/`, each
  at most 512 KiB. Keep every sidecar access bundle-relative. Never author
  `candidate.glb`, `measurement.json`, `preview/`, `region-diff.json`, or
  `source-changes.json`; trusted operations produce measured evidence and
  export artifacts.

## Reconstruction reasoning

You never see raw reference bytes. Instead you request one bounded
Reference Observation at a time. Two methods are available:

- `summary` — one closed geometric summary of the Canonical Reference.
  Arguments must be empty.
- `components` — up to a small bounded number of extracted geometric
  components. Arguments accept only `limit` (a positive integer).

```json
{
  "schema": "mesh-to-cad.agent-intent/1",
  "intent": "observe_reference",
  "args": {
    "reference_handle": "<opaque>",
    "observation": { "method": "summary", "args": {} }
  }
}
```

Every other observation method is `unsupported_operation`. Do not ask
for raw mesh access, file paths, or free-form measurements; those are
not available and asking for them is a closed error.

Use observation results plus residual evidence returned by the
supervisor (measurements, region diffs, previews) to form one
falsifiable repair hypothesis at a time. Cite what you observed, what
you would change in the candidate source, and what geometric fact
would refute the hypothesis. The residual facts the supervisor returns
inform your reasoning; they never prescribe an edit.

## Decision facts

Every successful `submit_step_zero` and `submit_repair` response
carries one closed **decision facts** object under the
`decision_facts` field:

```json
{
  "schema": "mesh-to-cad.decision-facts/1",
  "step_ordinal": 1,
  "parent_step_ordinal": 0,
  "accepted": false,
  "acceptance_state": "unaccepted",
  "residual_summary": {
    "objective_facts": {
      "global_depth_8_zero": false,
      "out_of_frame_clear": true,
      "no_evidence_conflict": true
    },
    "depth_8_missing_surface_count": 42,
    "depth_8_excess_surface_count": 0,
    "depth_8_surface_error_count": 42,
    "depth_8_surface_error_rate": 0.017,
    "repair_frontier": {
      "active_depth": 3,
      "missing_surface_count": 20,
      "excess_surface_count": 0,
      "surface_error_count": 20,
      "surface_error_rate": 0.125
    }
  },
  "repair_targets": {
    "returned": 3,
    "total": 3,
    "remaining": 0,
    "items": [
      {
        "target_key": "step-000001:target-0123456789abcdef",
        "mask_sha256": "<sha256>",
        "rank": 0,
        "kind": "interior",
        "bounds_canonical": {
          "min": [-0.5, -0.2, -0.1],
          "max": [0.4, 0.3, 0.2]
        },
        "missing_surface_count": 20,
        "excess_surface_count": 0,
        "surface_error_count": 20
      }
    ]
  },
  "preview": {
    "identity_sha256": "…",
    "render_variant": "step"
  },
  "change_from_parent": {
    "no_observable_geometry_change": false,
    "parent_accepted": false
  }
}
```

Fields and bounds:

- `step_ordinal` — non-negative integer identifying the just-published
  step.
- `parent_step_ordinal` — non-negative integer for repairs, `null`
  for Step 0.
- `accepted` and `acceptance_state` — `acceptance_satisfied` iff all
  three objective facts are true, else `unaccepted`.
- `residual_summary.objective_facts` — the exact three booleans that
  gate acceptance.
- `residual_summary.depth_8_*_surface_count` /
  `depth_8_surface_error_rate` — exact acceptance and residual-accounting
  scalars for depth 8. They do not select the repair layer (rate is in
  `[0, 1]`; NaN or infinite values fail closed).
- `residual_summary.repair_frontier` — the current action layer. Its
  `active_depth` is the coarsest depth with interior error, or `null` when
  the interior is clear. When it is `null`, all remaining frontier scalars
  are zero; exterior errors remain only in their `repair_targets` entries.
  Otherwise the remaining bounded scalars describe that interior layer.
  Use it with `repair_targets`, whose interior targets are grouped at this
  active depth while retaining exact depth-8 masks.
- `repair_targets` — `null` when acceptance is satisfied, otherwise a
  page of up to eight items that includes active-depth interior targets
  and may also include independent actionable exterior targets. Exterior
  targets are not part of the interior Repair Frontier. Each item
  carries the exact `target_key` and `mask_sha256` pair required by a
  `voxblame.repair-batch/1`, plus its rank, semantic kind,
  `bounds_canonical`, and bounded residual counts. Bounds are closed
  three-axis canonical `min`/`max` coordinates with finite values and
  `min <= max` on each axis; they apply to interior and exterior targets.
  Copy the pair unchanged into `selected_targets`;
  neither value is a path or raw mask content.
- `preview.identity_sha256` — the formal identity digest of the
  Measured Step's preview render; you may cite it in your assessment
  but cannot dereference it.
- `change_from_parent` — repair-only; reports whether the
  Measured Step observably changed vs. its parent and whether the
  parent was itself accepted.

Rules for using decision facts:

- Treat them as read-only. They are the only measured facts you may
  cite; you never override them, and you never derive acceptance from
  authored content.
- Base your next repair hypothesis on them: `repair_frontier` and
  `repair_targets` identify the active action layer and target facts;
  depth-8 scalars remain the exact acceptance accounting;
  `repair_targets` names the top-ranked residual regions by kind and
  supplies only the stable selection identities needed by the repair
  plan.
- If `change_from_parent.no_observable_geometry_change` is true after
  a repair, your source edit did not move observable geometry — stop
  and reconsider before spending another attempt.
- If `acceptance_state` is `acceptance_satisfied`, do not start
  another repair; proceed to `select_and_finalize`.
- The decision-facts response is capped alongside the rest of the
  intent envelope; there is no larger view. Unknown fields, extra
  keys, or non-finite numbers are closed errors on our side, not on
  yours.

## Bounded loop shape

The bounded loop the supervisor enforces is:

1. `workspace_status` to read the initial permitted intents and budgets.
2. Author an initial plan at `/candidate/plan.json` and pass it to
   `start_attempt`. Then write your Step 0 source under
   `/candidate/work/source/`.
3. Use `run_candidate_tool` to build, preview, and measure the
   candidate. Each call returns fresh handles.
4. Use `submit_step_zero` to submit the measured initial step. The
   supervisor retires that Attempt and resets `/candidate/work`.
5. Loop: form a repair hypothesis and replace `/candidate/plan.json`
   with a `voxblame.repair-batch/1` that selects exact
   `{target_key, mask_sha256}` pairs from the parent's decision facts.
   Start the next Attempt with an explicit parent step handle; the supervisor reseeds
   `/candidate/work/source/` with the parent's source. Edit it,
   run the registered tools for the child, then `submit_repair`.
   Stop when residuals establish acceptance, when no further coherent
   repair is plausible, or when the supervisor reports
   `budget_exhausted`.
6. `select_and_finalize` with the strongest plausible step handle and
   authored notes.

You always read `permitted_next_intents` in the previous response and
issue only an intent it lists. Any other intent returns
`state_conflict`.

## Stop reasons and honesty

Never claim acceptance, provenance, or verification you did not
observe in an intent response. When you stop early, name one honest
stop reason drawn from what the responses tell you:

- `acceptance_reached` — the last submitted step's residuals meet the
  acceptance signal in the supervisor result.
- `no_feasible_repair` — you cannot form a coherent falsifiable
  hypothesis from current evidence.
- `budget_exhausted` — the surface returned this classification.
- `unsupported_domain` — the domain cannot be represented honestly as
  STEP-first parametric CAD.

Report exactly what handles the supervisor returned. Do not invent
identifiers.

## Progressive references

- `references/candidate-authoring.md` — patterns for writing STEP-first
  parametric candidate source under `/candidate/work/source/`.
- `references/assessment.md` — how to author
  `/candidate/work/assessment.json` from returned decision facts.
- `references/agent-selection-claim.md` — the bounded selection-claim
  schema `select_and_finalize` reads from your selection handle.
