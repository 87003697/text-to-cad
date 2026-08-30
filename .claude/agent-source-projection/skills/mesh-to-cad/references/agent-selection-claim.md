# Agent selection claim

`select_and_finalize` reads a **bounded semantic claim** from the JSON file
your selection handle points at. The supervisor pairs it with the Selected
Step your `step_handle` names and constructs the final selection itself.
Everything the supervisor needs to trust is derived from the Selected Step;
your claim contributes only the semantic observation and the honest stop
reason.

## Schema

```json
{
  "schema": "mesh-to-cad.agent-selection-claim/1",
  "preview_observation": "<concise prose>",
  "stop_reason": "<one enum value>",
  "conflict": false,
  "conflict_details": null,
  "rationale": "<concise prose>"
}
```

The document has **exactly these six keys**. An extra key, a missing key,
or a schema mismatch is a closed error and the intent fails without
publishing anything.

### `preview_observation` (string, 1..4096 chars)

One or two sentences describing the Selected Step from returned Reference
Observations and decision facts. The field name is fixed by the schema, but
fixed-client transport has no preview image attachment. Do not restate
acceptance, cite hashes, or name step ordinals or evidence paths. The
supervisor already knows those and rebuilds them from the Selected Step.

### `stop_reason` (enum)

One of the seven honest reasons drawn from what the intent responses told
you:

- `acceptance_satisfied` — the Selected Step's `decision_facts.accepted`
  was `true`. If the Selected Step is unaccepted, do **not** use this
  value; the supervisor rejects it as an identity conflict.
- `cycle_limit` — the latest `workspace_status` reported
  `budgets.remaining_cycles` as zero before acceptance. Per-intended-step
  Attempt or tool-failure limits do not justify this reason.
- `no_feasible_strategy` — no coherent falsifiable repair hypothesis
  remained under the evidence you had.
- `representation_limit` — the target cannot be represented honestly as
  the STEP-first parametric form the skill supports.
- `modeling_intent_conflict` — the returned observations and the modeling intent
  disagree in a way you cannot reconcile from the current source.
- `repeated_ineffective_strategy` — repair attempts did not change
  observable geometry across cycles.
- `tool_failure` — a registered candidate operation returned a closed
  failure that blocked further progress.

Every other value is rejected. If none of these describes what actually
happened, stop and report the situation truthfully; do not choose the
closest label.

### `conflict` (bool) and `conflict_details` (string or null)

Set `conflict: true` **only** when you observed a material semantic
mismatch between the Selected Step and the requested modeling intent
that finalization should not paper over — for example returned observations
and decision facts identify a feature that contradicts the intent. When you do, provide a
concise 1..4096-char `conflict_details` string. The supervisor fails
`select_and_finalize` closed with `agent_semantic_conflict`; nothing
is published.

When there is no conflict, `conflict` must be `false` and
`conflict_details` must be `null`. A non-null `conflict_details` on a
clear claim is a closed error.

### `rationale` (string, 1..4096 chars)

One or two sentences explaining why this Selected Step is the honest
choice given the stop reason. Cite what you observed and what you
weighed. Do not cite hashes, ordinals, evidence paths, provider facts,
or acceptance status; the supervisor owns all of those.

## What must not appear in the claim

The claim schema is closed on purpose. The following are not fields you
may add, and adding any of them is a closed error:

- `evidence`, `evidence_sha256`, `measurement_path`, or any hash.
- `selected_step`, `step_id`, `considered_steps`, `parent_step`.
- `accepted`, `acceptance_state`, or any acceptance flag.
- `preview_identity_sha256` or any other identity digest.
- Any provider fact, authority schema, or supervisor-owned field.

The Selected Step named by your `step_handle` is the only trusted
carrier of those facts. Attempting to smuggle any of them through the
claim is refused before any tool runs.

## Choosing the step handle

`step_handle` is opaque. It must be one of the step handles the
supervisor returned to you from `submit_step_zero` or `submit_repair`
in this Attempt sequence. Any published and validated Measured Step
returned by the supervisor is selectable, including a historical step
that is no longer a graph head when it remains the strongest result.
Pass the opaque handle itself; an ordinal or path is not a selection
identity.

Choose the honest strongest returned step that best matches your stop
reason: an accepted step if you paired it with `acceptance_satisfied`;
otherwise the strongest unaccepted step you would defend on its own
merits.
