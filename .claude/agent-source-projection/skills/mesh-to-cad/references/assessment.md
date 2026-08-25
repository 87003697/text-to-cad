# Assessment authoring

`/candidate/work/assessment.json` is the one Agent-authored file that
travels with a Measured Step. It records, in your own words, the
falsifiable repair hypothesis that motivated this Attempt's source
edit and how you plan to check it against the next measured facts.

## Fixed schema

The assessment document is a small closed JSON object bound to the
schema tag `mesh-to-cad.assessment/1`:

```json
{
  "schema": "mesh-to-cad.assessment/1",
  "from_step": 0,
  "to_step": 1,
  "preview_observation": "…",
  "summary": "…"
}
```

Fields:

- `schema` — must be exactly `mesh-to-cad.assessment/1`. Any other
  value is rejected.
- `from_step` — the parent step ordinal you branched from. Use `null`
  for Step 0. For a repair it must equal the `parent_step_ordinal`
  in the decision facts the supervisor returned for the current
  Attempt.
- `to_step` — the step ordinal this Attempt will publish. It must
  equal the `step_ordinal` in the decision facts.
- `preview_observation` — a short human-language note about the
  candidate preview you inspected before submitting. Free text, but
  scoped to what you observed in the returned preview identity, not
  raw geometry.
- `summary` — a short human-language statement of the falsifiable
  hypothesis: what you edited in `work/source/`, why that edit should
  change a specific residual, and which decision-fact fields would
  refute the hypothesis on the next measurement.

The file has no other keys. Extra keys, missing required fields,
or non-string values fail closed.

## Authoring flow

1. Read the intent response for the previous submission. The
   `decision_facts` object under it contains every measured fact you
   are allowed to cite.
2. From `residual_summary.objective_facts` and `repair_targets`,
   pick one residual you can plausibly move with a source edit.
3. Make that edit under `/candidate/work/source/`. Do not touch any
   other file the supervisor named as its own.
4. After `run_candidate_tool` produces the preview, write
   `/candidate/work/assessment.json` with:
   - `from_step` bound to the current parent, `to_step` bound to the
     step you are about to publish;
   - a `summary` naming which of the three objective facts you expect
     to flip, which repair-target `kind` you targeted, and what the
     next decision-fact reading should show if the hypothesis holds;
   - a `preview_observation` grounded only in what the preview
     identity's rendered variant let you see.
5. Submit through the appropriate intent. The supervisor uses your
   assessment as authored notes only.

## What assessment is not

- **Assessment cannot override measured facts.** The next intent
  response's `decision_facts` remains the sole source of truth for
  acceptance, residuals, and observable change. If your assessment
  claims a residual is fixed but `residual_summary.objective_facts`
  says otherwise, the objective facts win.
- **Assessment cannot rename or renumber steps.** `from_step` and
  `to_step` are bound to the ordinals in the supervisor's decision
  facts; inventing other numbers is rejected.
- **Assessment cannot reference internal identifiers.** Do not paste
  handles, digests other than the preview identity, or paths outside
  `/candidate/work/`. Cite semantic facts, not identifiers.
- **Assessment cannot substitute for a submission.** Writing the
  file has no effect until you invoke `submit_step_zero` or
  `submit_repair` with the current handles.

## One worked example

Suppose the last Step 0 response reported:

- `acceptance_state`: `unaccepted`
- `residual_summary.objective_facts.global_depth_8_zero`: `false`
- `repair_targets.items[0].kind`: `excess`

You edit `work/source/model.py` to trim a boss you believe is
producing the top excess region, rebuild and preview, then write:

```json
{
  "schema": "mesh-to-cad.assessment/1",
  "from_step": 0,
  "to_step": 1,
  "preview_observation": "Preview shows the trimmed boss no longer overhangs the plate.",
  "summary": "Trimmed boss diameter by 2 mm to eliminate the rank-0 excess region; expect global_depth_8_zero to become true and depth_8_error_count to drop meaningfully. Refuted if the same excess kind reappears at rank 0."
}
```

`submit_repair` then returns the next `decision_facts` object, and
those measured facts — not this assessment — decide whether the
hypothesis held.
