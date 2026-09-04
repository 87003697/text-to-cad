---
name: mesh-to-cad
description: Author STEP-first parametric candidate source and steer bounded reconstruction through the closed Agent Intents listed below.
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
- Treat Polygon as XY by default; use explicit `Plane.YZ` (or an explicit
  coordinate mapping) for semantic YZ profiles, and verify final canonical
  XYZ bounds after every transformed placement. If a residual lies outside
  Reference bounds or a transformed component has an unexpected global bound,
  fix source coordinates before widening the Reconstruction Spec. Component
  targets still require strict positive-volume overlap.
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

The supervisor exposes only the closed intents listed below. Each intent takes
opaque handles the supervisor gave you in an earlier response and returns
a closed result plus the list of intents permitted next.

- `workspace_status` — read the current bounded workflow state, the
  active budgets, the workspace identity, and the intents permitted next. A
  current preterminal publication whose original response was not received may
  additionally return `publication_recovery`, the exact published response.
- `start_attempt` — begin one bounded Attempt from an authored plan
  handle. Optionally branch from a parent step handle.
- `run_candidate_tool` — for Step 0, ask the supervisor to run the fixed
  canonical build on the current Attempt's candidate. You never invoke the
  build tool yourself.
- `submit_step_zero` — submit the measured initial step through the
  supervisor using only the workspace, attempt, and candidate handles.
  The supervisor owns the trusted candidate tree and its evidence; you
  never name or select evidence handles.
- `evaluate_repair_draft` — snapshot the current Repair source and assessment,
  build and evaluate that private snapshot, and retain one immutable draft
  with closed feedback (maximum eight per intended step).
- `submit_repair` — publish one selected evaluated draft using its opaque
  draft handle.
- `abandon_repair_attempt` — retire the active Repair Attempt early or after
  draft exhaustion while preserving the intended-step evaluation budget.
- `inspect_formal_preview` — use the opaque `preview_handle` returned by a
  published Step to inspect its formal image through the Agent Surface MCP
  tool. In code mode call `mcp__agent_surface__inspect_formal_preview`; native
  Responses uses namespace `mcp__agent_surface` with child
  `inspect_formal_preview`. It returns an image block, never a path or image
  bytes in JSON.
- `inspect_repair_targets` — use a published Step's opaque `step_handle` and
  the exact `next_offset` returned by the previous page to read every committed
  Repair Target through the fixed client. It returns only the public
  `{rank, kind, bounds_canonical}` triples.
- `observe_target_section` — use a published Step's opaque `step_handle` and
  one public target rank to compare Reference and candidate core section
  profiles plus target-local Active-Depth occupancy.
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

## Fixed-client transport

Invoke the closed JSON intents through ordinary `exec_command`, running only the
fixed client `python3 /agent-surface/client.py`. Feed it exactly one closed JSON
object on stdin and read its one JSON response before issuing another intent.
If a long-running call returns an `exec_command` session ID before its JSON
response, normally poll that exact session through `write_stdin` with empty
input until the client exits and returns the response. If a completed
publication response is unavailable, call `workspace_status`; its optional
`publication_recovery` is the exact published response. Use it without
resubmitting W1. Do not issue another intent while that session is live.
When wrapping tool calls in `functions.exec` JavaScript, preserve and inspect
the complete `exec_command` result, including `session_id`; never print only
`result.output`. If `session_id` is present, immediately call
`tools.write_stdin` on that same session and retain the complete returned
result. If the wrapper would flatten the result, emit `JSON.stringify(result)`
so no session metadata is lost. Keep all intent calls strictly serial.
Treat `submit_step_zero`, `submit_repair`, and `evaluate_repair_draft` as
potentially long-running calls: the `functions.exec` wrapper must emit
`text(JSON.stringify(r))`, never only `text(r.output)`. If `session_id` is
present, poll that same session until the client returns its JSON response;
issue no other intent while it is live. If the wrapper returns empty output
without a session ID, stop and report a transport issue rather than issuing
`workspace_status` concurrently.
Every invocation must include the request on stdin. Do not construct a request
in a shell or JavaScript variable and then run the client without feeding that
variable. For the first call, replace the placeholder with the opaque
`workspace_handle` from `/candidate/bootstrap.json` and copy this complete
invocation:

If the client returns `invalid_request` at path `$.request`, retry that same
intent once only when the immediately preceding invocation visibly contains a
heredoc or handle-interpolation error that you can correct locally. In
particular, a single-quoted heredoc (`<<'JSON'`) does not expand a shell
`$(python3 ...)` substitution or `${HANDLE_JSON}`; use the documented variable
expansion form, then resend the unchanged intent envelope once. Treat any
other `invalid_request` as a closed supervisor result and do not retry it.

```bash
python3 /agent-surface/client.py <<'JSON'
{"schema":"mesh-to-cad.agent-intent/1","intent":"workspace_status","args":{"workspace_handle":"<workspace_handle from /candidate/bootstrap.json>"}}
JSON
```

Use this complete heredoc form for every JSON intent, changing only the closed
request envelope and its documented arguments. After a published Step returns a
`preview_handle`, emit no text before calling the only MCP tool you may use:
code mode calls `mcp__agent_surface__inspect_formal_preview`; native Responses
uses namespace `mcp__agent_surface` with child `inspect_formal_preview`. Never
use a dotted server/tool spelling. Inspect its image block before creating or
updating the Reconstruction Spec, and record only visual facts actually visible
there. Do not use `tool_search`, another client, Workspace helper,
path-discovery operation, or direct socket.

The `args` object must have exactly the fields for its intent:

- `workspace_status`: `workspace_handle`.
- `start_attempt`: `workspace_handle`, `plan_handle`, and, only for a repair,
  `parent_step_handle`.
- `run_candidate_tool`: `workspace_handle`, `attempt_handle`,
  `candidate_handle`, `operation_handle`.
- `submit_step_zero`: `workspace_handle`, `attempt_handle`, `candidate_handle`.
- `evaluate_repair_draft`: `workspace_handle`, `attempt_handle`,
  `candidate_handle`, `evaluation_ticket`.
- `submit_repair`: `workspace_handle`, `attempt_handle`, `draft_handle`.
- `abandon_repair_attempt`: `workspace_handle`, `attempt_handle`.
- `inspect_repair_targets`: `step_handle`, `offset`.
- `observe_target_section`: `step_handle`, `rank`.
- `select_and_finalize`: `workspace_handle`, `step_handle`,
  `selection_handle`, `notes_handle`.
- `observe_reference`: `reference_handle` and an `observation` object that is
  either `{"method":"summary","args":{}}` or
  `{"method":"section_profile","args":{}}`.

Send only one intent after reading the preceding client response. On an error,
preserve its classification and do not retry blindly.

Every successful client response is one JSON object with the response envelope:

```json
{
  "ok": true,
  "response": {
    "schema": "mesh-to-cad.agent-response/7",
    "intent": "<same intent>",
    "result": { "...": "..." }
  }
}
```

Each intent has its own closed result shape:

- `workspace_status`: `state`, `workspace_identity`, `budgets`,
  `permitted_next_intents`, and, only for an unreceived current published
  response, `publication_recovery` with that published response's closed shape.
  `budgets` is exactly `{remaining_cycles, attempts_per_intended_step,
  tool_failures_per_intended_step}`. `remaining_cycles` is the only global
  Repair capacity. The other two values are static limits for each intended
  step, not remaining balances or a shared pool.
- `start_attempt`: `state`, `attempt_handle`, `candidate_handle`,
  `capability_bundle_handle`, `permitted_next_intents`.
- Repair `start_attempt` additionally returns `evaluation_ticket` and
  `draft_budget` `{used, remaining, maximum:8}`.
- `evaluate_repair_draft`: on success, `state`, `draft_handle`, closed
  `feedback`, `next_evaluation_ticket` (or `null` at exhaustion), and
  `permitted_next_intents`. An admitted evidence failure consumes its slot and
  returns `classification`, a closed `subtype`, and the next ticket; an invalid
  or stale ticket consumes no slot. Replaying a completed ticket returns its
  cached result.
- `abandon_repair_attempt`: `state`, `permitted_next_intents`.
- `run_candidate_tool`: `state`, `candidate_handle`, `result_handle`,
  `permitted_next_intents`.
- `submit_step_zero`: `state`, `step_handle`, `preview_handle`, `decision_facts`,
  `permitted_next_intents`.
- `submit_repair`: on publication, `state`, `step_handle`, `preview_handle`, `cycle_handle`,
  `decision_facts`, `permitted_next_intents`; on its closed evidence failure,
  `state`, `classification`, `subtype`, `permitted_next_intents`. Its subtype
  is one of `provider_execution_failed`, `voxblame_output_invalid`,
  `preview_output_invalid`, `region_diff_invalid`, or `source_changes_invalid`.
  Use it only to choose a permitted next action; it carries no host detail.
- `select_and_finalize`: `state`, `final_delivery_handle`,
  `permitted_next_intents`.
- `inspect_formal_preview`: `state`, `preview_handle`, `permitted_next_intents`, plus an MCP image block.
- `inspect_repair_targets`: `schema`, `step_ordinal`, `total`, `returned`,
  `remaining`, `offset`, `next_offset`, `items`. Its schema is
  `mesh-to-cad.repair-target-page/1`; every item is exactly
  `{rank, kind, bounds_canonical}`.
- `observe_target_section`: exactly `schema`, `rank`, `reference`, `candidate`,
  and `local_occupancy`, with schema
  `mesh-to-cad.target-section-observation/3`. Each side contains one `core`
  with only `triangle_count` and fixed X/Y/Z eight-slab `profiles`.
  `local_occupancy` is `null` for exterior targets; otherwise it contains
  `target:[1,1,1]` and separate Reference and candidate 3×3×3 boolean/null
  cubes indexed `[x][y][z]`. `true` is occupied, `false` is in-frame empty,
  and `null` is outside the canonical frame.

Repair draft feedback is `mesh-to-cad.repair-draft-feedback/1`. `before` and
`after` contain only the parent and draft Active-Depth missing/excess surface
counts; signed `delta` is `after - before`. `target_change_preview` contains
`resolved`, `persisted`, and `new`, each with `total`, `returned`, `remaining`,
and at most eight `{kind,bounds_canonical}` items. Use it to compare local
surface hypotheses. It does not expose a depth, component identity, or fixed
Depth-8 verdict, and it does not count as inspecting a committed target for
`no_feasible_strategy` finalization.

`observe_reference` is not a workflow-state response. Its `result` is exactly
`{"observation":{"method":"<method>","value":{...}}}`. A `summary` value
contains canonical-frame, bounds, count, quality, and aggregate geometry facts.

A supervisor/handler error response is:

```json
{
  "ok": false,
  "schema": "mesh-to-cad.agent-error/1",
  "error": { "classification": "<enum>",
              "path": "<jsonpath>",
              "detail": "<enum>" }
}
```

If the fixed client cannot parse its stdin JSON before contacting the
supervisor, it instead returns this local parse-failure shape:

```json
{
  "ok": false,
  "error": {
    "schema": "mesh-to-cad.agent-error/1",
    "error": { "classification": "invalid_request",
               "path": "$.request",
               "detail": "invalid_request" }
  }
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
- Do not write, name, remove, or otherwise touch `work/candidate.glb` or any
  export artifact — the supervisor builds them from your source. For Step 0,
  once the build succeeds, submit it instead of calling `run_candidate_tool`
  again. Repair evaluation always builds a fresh private snapshot and never
  requires export cleanup in `/candidate/work`.
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
      {"rank": 0, "kind": "missing", "bounds_canonical": {"min": [-0.5, -0.5, -0.5], "max": [0.0, 0.0, 0.0]}}
    ],
    "planned_edits": [
      {
        "edit_key": "edit-key",
        "target_ranks": [0],
        "spec_region_id": "component.fuselage",
        "description": "Agent-authored modeling change"
      }
    ],
    "rationale": "Why these targets form one coherent modeling problem.",
    "preview_observation": "What the Reference Observation and parent decision facts establish before editing."
  }
  ```

  `from_step` must be the current parent step. Target ranks and edit keys are
  unique; each selected target repeats only its returned rank, kind, and
  canonical bounds. Every selected target must be covered by one or more
  planned edits, and every target/edit list and prose field must be nonempty.
  When Reconstruction Spec is enabled, every Planned Edit must name exactly one
  existing Component `id` in `spec_region_id`. Each target named by that edit
  must have strictly positive three-axis volume overlap with that Component's
  `bounds_canonical`; face, edge, or point contact does not count.
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
    "components": [
      {"id": "component.fuselage", "bounds_canonical": {"min": [-0.12, -0.5, -0.1], "max": [0.12, 0.5, 0.16]}},
      {"id": "component.main-wing-left", "bounds_canonical": {"min": [-0.5, -0.25, -0.05], "max": [0.0, 0.0, 0.08]}},
      {"id": "component.main-wing-right", "bounds_canonical": {"min": [0.0, -0.25, -0.05], "max": [0.5, 0.0, 0.08]}},
      {"id": "component.tailplane", "bounds_canonical": {"min": [-0.12, 0.25, -0.03], "max": [0.12, 0.375, 0.1]}},
      {"id": "component.vertical-fin", "bounds_canonical": {"min": [-0.03, 0.375, 0.05], "max": [0.03, 0.5, 0.2]}}
    ],
    "features": [{"id": "feature.opening", "certainty": "inferred"}],
    "relations": [
      {
        "id": "relation.opening-part-of-body",
        "kind": "part_of",
        "from": "feature.opening",
        "to": "component.fuselage"
      }
    ]
  }
  ```

  Components require an `id` and `bounds_canonical`; Features require an `id`;
  Relations require `id`, `kind`, `from`, and `to`. Component bounds are
  finite three-axis canonical `{min,max}` arrays with `min < max` on every axis.
  IDs are globally unique, relation endpoints name an
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

## Candidate geometry guardrails

Use build123d constructions whose returned shape preserves every intended
solid:

- Extrude a polygon plate directly:
  `extrude(Polygon(*points, align=None), amount=thickness)`. Wrapping that
  polygon as `Face(Polygon(...))` produces an empty face in the installed
  runtime; if an operation specifically needs a face, select the polygon's
  existing `.faces()[0]`.
- Keep independently named body, wing, and tail solids with
  `Compound([body, wing, tail])`. Use a boolean only when the hypothesis
  requires a watertight fused solid; `+` is a boolean composition operator,
  not an assembly container.
- For nonuniform scaling, keep the primitive and scale operation private,
  then return or explicitly collect the transformed value:

  ```python
  from build123d import Compound, Location, Mode, Sphere, scale

  body = scale(
      Sphere(1.0, mode=Mode.PRIVATE),
      by=(sx, sy, sz),
      mode=Mode.PRIVATE,
  ).moved(Location((x, y, z)))
  result = Compound([body, wing, tail])
  ```

When changed source returns
`change_from_parent.no_observable_geometry_change=true`, first treat it as a
returned-shape or construction failure. Check for empty shapes, unintended
boolean composition, and discarded transform results before changing chord,
height, curvature, or another geometric parameter without evidence.

## Reconstruction reasoning

You never see raw reference bytes. Instead you request one bounded
Reference Observation at a time. The available method is:

- `summary` — one closed geometric summary of the Canonical Reference.
- `section_profile` — a fixed 8-slab profile on each canonical X/Y/Z axis.
  Each profile names its two occupied axes. Each slab has its canonical
  coordinate interval, occupied extents in that named axis order,
  centroid-partitioned surface-area fraction, and `mean_abs_normal` keyed by
  X/Y/Z: the area-weighted mean absolute unit-normal components, not a
  partitioning histogram. Its value schema is
  `meshscope.reference-section-profile/1`; its response is bounded to 64 KiB.
  It is a bounded whole-object cue, not components.
  Arguments must be empty.

```json
{
  "schema": "mesh-to-cad.agent-intent/1",
  "intent": "observe_reference",
  "args": {
    "reference_handle": "<opaque>",
    "observation": { "method": "section_profile", "args": {} }
  }
}
```

Prohibited raw-access methods such as `components` are
`unsupported_operation`; other unrecognized method names are `unknown_method`.
Do not ask for raw mesh access, file paths, or free-form measurements; those
are not available and asking for them is a closed error.

For fixed-client transport, use `summary` for canonical-frame, bounds, count,
quality, and aggregate geometry facts; use `section_profile` for fixed
whole-object shape cues. Neither returns an image attachment. After a published
Step, use the registered `inspect_formal_preview` MCP tool and record only
visual facts actually visible in its returned image block.

Use observation results plus residual evidence returned by the
supervisor (measurements, region diffs, and decision facts) to form one
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
  "schema": "mesh-to-cad.decision-facts/2",
  "step_ordinal": 1,
  "parent_step_ordinal": 0,
  "accepted": false,
  "acceptance_state": "unaccepted",
  "residual_summary": {
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
        "rank": 0,
        "kind": "missing",
        "bounds_canonical": {
          "min": [-0.5, -0.2, -0.1],
          "max": [0.4, 0.3, 0.2]
        }
      }
    ]
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
- `accepted` and `acceptance_state` — host-only exact acceptance. Do not infer
  or reconstruct its depth-8 evidence.
- `residual_summary.repair_frontier` — the current action layer. Its
  `active_depth` is the coarsest depth with interior error, or `null` when
  the interior is clear. When it is `null`, all remaining frontier scalars
  are zero; exterior errors remain only in their `repair_targets` entries.
  Otherwise the remaining bounded scalars describe that interior layer.
  Use it with `repair_targets`, whose directional interior targets are
  individual net-error cells at this active depth.
- `repair_targets` — `null` when acceptance is satisfied, otherwise a
  page of up to eight items that includes active-depth directional targets
  and may also include independent actionable exterior targets. Exterior
  targets are not part of the interior Repair Frontier. Each item
  carries only rank, semantic kind, and active-depth-cell
  `bounds_canonical`. Bounds are closed
  three-axis canonical `min`/`max` coordinates with finite values and
  `min <= max` on each axis; they apply to interior and exterior targets.
  `missing` means add geometry in the bounded cell, `excess` means remove or
  shrink geometry there, and `exterior` means recover geometry outside the
  canonical frame. Repeat the returned `kind` unchanged in every selected
  target; do not reinterpret its direction.
- `change_from_parent` — repair-only; reports whether the
  Measured Step observably changed vs. its parent and whether the
  parent was itself accepted.

Rules for using decision facts:

- Treat them as read-only. They are the only measured facts you may
  cite; you never override them, and you never derive acceptance from
  authored content.
- Base your next repair hypothesis on them: `repair_frontier` and
  `repair_targets` identify the active action layer and coarse target facts.
  When `repair_targets.remaining` is positive, call
  `inspect_repair_targets` with offset `0` on the same `step_handle`, then
  follow each returned `next_offset` until it is `null`, before selecting any
  target. Treat the concatenated rank order as
  the complete attention order; do not guess a rank or treat the first eight
  as a priority shortlist. Copy a selected target's public
  `{rank, kind, bounds_canonical}` triple unchanged into the repair plan.
  Before writing the plan, call `observe_target_section` for the small set of
  competing or semantically relevant targets you are considering. Do not
  observe every target, but observe at least one target for each distinct
  geometry pair under consideration. Use the separate occupancy cubes to read
  add/remove polarity, local continuation, and canonical-frame boundaries;
  use `core` profiles for sectional geometry and normals. Exterior targets
  expose only the core. This local geometry cannot change `kind`,
  replace the Reconstruction Spec, identify a Component, establish semantic
  ownership, establish long-range connectivity or thickness, or turn normals
  into a semantic label. If any profile used by
  your hypothesis has only one or two triangles, treat it as
  `ambiguous_low_sample` and make no directional or semantic claim from its
  normals.
- Compare candidate frontiers lexicographically: a greater `active_depth`
  is better; at the same depth, a smaller `surface_error_count` is better.
  Equal depth and error count is a tie. Use missing and excess counts for
  diagnosis, not for additional ranking.
- When the baseline has only `excess` targets and target-local or mixed
  polarity evidence has not established a need for reconstruction, make each
  repair draft one small change to one existing parameter, such as a relative
  placement or thickness adjustment. Do not combine lift and thinning, redraw
  topology, or make a broad span trim in one draft. Use the evaluation's
  lexicographic `before`/`after`/`delta` feedback to retain or change strategy;
  if the child is not lexicographically better, return to the best parent Step.
  An introduced `missing` target is a refutation and diagnostic signal, not an
  automatic rollback condition.
- When missing and excess targets occupy the same local region, treat the
  mixed polarity as a falsification signal for the boundary or size
  hypothesis. Re-evaluate that hypothesis against the local evidence, and
  use the adjacent opposite-polarity target as the explicit refutation
  condition for the next falsifiable repair.
- If `change_from_parent.no_observable_geometry_change` is true after
  changed source, diagnose the returned shape and construction first as
  specified above before spending another attempt.
- If `acceptance_state` is `acceptance_satisfied`, do not start
  another repair; proceed to `select_and_finalize`.
- The decision-facts response carries the first target page. Read later pages
  only through `inspect_repair_targets`; unknown fields, extra keys, or
  non-finite numbers are closed errors on our side, not on yours.

## Bounded loop shape

The bounded loop permits at most ten Repair Cycles after Step 0. Each intended
step permits at most three Attempts and two tool failures. Step 0 is not a
Repair Cycle. The supervisor enforces this shape:

1. `workspace_status` to read the initial permitted intents and budgets.
2. Author an initial plan at `/candidate/plan.json` and pass it to
   `start_attempt`. Then write your Step 0 source under
   `/candidate/work/source/`.
3. Use `run_candidate_tool` to build, preview, and measure the
   candidate. Each call returns fresh handles.
4. Use `submit_step_zero` to submit the measured initial step. The
   supervisor retires that Attempt and resets `/candidate/work`.
5. Inspect the returned formal preview. If the first Repair Target page has
   `remaining > 0`, call `inspect_repair_targets` at offset `0`, then follow
   every `next_offset` on the same Step before selecting a target. Then create
   or update the Reconstruction Spec. Observe the few competing or semantically
   relevant target ranks with `observe_target_section` before forming a repair
   hypothesis, including at least one per distinct geometry pair; use the
   occupancy cubes for local lattice adjacency and the core for sections and
   normals.
   Preserve low-sample ambiguity and do not infer Components or semantics from
   normals. Replace
   `/candidate/plan.json` with a `voxblame.repair-batch/1` that repeats the
   selected coarse target's `{rank, kind, bounds_canonical}` facts.
   Bind every Planned Edit to one existing semantic Component with
   `spec_region_id`; use `target_ranks` only for targets whose canonical bounds
   have strictly positive volume overlap with that Component's canonical bounds.
   Start the next Attempt with an explicit parent step handle; the supervisor reseeds
   `/candidate/work/source/` with the parent's source. Edit source and assessment,
   then use the returned ticket with `evaluate_repair_draft`. Compare its bounded
   Active-Depth feedback with other evaluated drafts. Submit one retained
   `draft_handle`, or call `abandon_repair_attempt` to change strategy. Each
   evaluation builds in a fresh private stage; never create or remove a Repair GLB.
   Maintain the best-so-far result by Active Depth using lexicographic
   comparison. If a child is not better than that result, start the next
   Attempt from the best result's opaque parent handle. Stop when residuals
   establish acceptance, when no further coherent repair is plausible, or
   when no permitted repair remains. Decide whether an action is currently
   callable only from `permitted_next_intents`; never infer admission from a
   local per-intended-step limit. An admitted evaluation failure consumes one
   slot and replays exactly for its completed ticket; a stale or invalid ticket
   consumes none. A retained successful draft remains submittable after a later
   evaluation failure or budget exhaustion.
   For every Repair Attempt, rebind the plan to the chosen parent's current
   target facts, set `from_step` to that parent's ordinal, and set assessment
   `to_step` to one greater than the maximum published returned step ordinal;
   this remains true across historical branches.
   If `start_attempt` returns `attempt_rejected` for a closed constraint that
   your own plan/spec facts can verify (for example, a selected target has no
   strict positive-volume overlap with its `spec_region_id` bounds), correct
   the plan/spec and retry `start_attempt` for the same parent when that intent
   is permitted. Do not retry an unchanged request or loosen the supervisor
   contract; stop only when the constraint cannot be repaired from public
   facts or the response is a closed state with no permitted retry.
6. `select_and_finalize` with the strongest returned opaque step handle
   and authored notes.

You always read `permitted_next_intents` in the previous response and
issue only an intent it lists. Any other intent returns
`state_conflict`.

## Stop reasons and honesty

Never claim acceptance, provenance, or verification you did not observe in an
intent response. Use the stop reasons in
`references/agent-selection-claim.md`. Use `cycle_limit` only when the latest
`workspace_status` reports `remaining_cycles: 0`. A per-intended-step Attempt
or tool-failure limit is never evidence that the global Repair Cycle budget is
exhausted. If cycles remain but no coherent repair is available, use
`no_feasible_strategy`. Before selecting that stop reason for an unaccepted
Step with public Repair Targets, observe at least one public target on that
exact Selected Step. An observation of a historical Step does not satisfy this
gate. If `select_and_finalize` returns `state_conflict`, call
`workspace_status`, observe a public target from the Selected Step, then retry
the same `no_feasible_strategy` selection or continue repairing.
Do not use `representation_limit` solely because a coarse Step 0 has a large
error or a simple primitive fit. When public Repair Targets and repair budget
remain, first test at least one coherent decomposition or repair hypothesis;
reserve `representation_limit` for evidence that the target cannot be
represented honestly in the supported STEP-first form.

Report exactly what handles the supervisor returned. Do not invent
identifiers.

## Progressive references

- `references/candidate-authoring.md` — patterns for writing STEP-first
  parametric candidate source under `/candidate/work/source/`.
- `references/assessment.md` — how to author
  `/candidate/work/assessment.json` from returned decision facts.
- `references/agent-selection-claim.md` — the bounded selection-claim
  schema `select_and_finalize` reads from your selection handle.
