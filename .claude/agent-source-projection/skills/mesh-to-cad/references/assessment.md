# Assessment authoring

`/candidate/work/assessment.json` is the one Agent-authored file that
travels with a Repair Cycle's Measured Step. It records, in your own words,
the falsifiable repair hypothesis that motivated this Attempt's source edit
and how you plan to check it against the next measured facts.

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
- `from_step` — the selected parent step ordinal. Copy `step_ordinal` from
  the decision facts returned by that selected parent's submission. Do not
  use `parent_step_ordinal`; that names the selected parent's parent.
- `to_step` — the intended child step ordinal for the current Repair Attempt,
  as bound by the Attempt/plan lifecycle. The current Attempt has no decision
  facts yet; W1 checks this value against the intended child at submission.
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
2. Read `residual_summary.repair_frontier.active_depth` first. When it is
   non-null, use the active-depth interior targets and their
   `bounds_canonical` values to pick one residual you can plausibly move with
   a source edit. Also inspect any exterior targets on the same page: they
   are independent actionable residuals, not part of the interior frontier.
   Use depth-8 scalars only for exact acceptance and residual accounting, not
   to select the repair layer or target.
3. Make that edit under `/candidate/work/source/`. Do not touch any
   other file the supervisor named as its own.
4. After `run_candidate_tool` produces the preview, write
   `/candidate/work/assessment.json` with:
   - `from_step` copied from the selected parent's `decision_facts.step_ordinal`;
     `to_step` bound to the current Repair Attempt's intended child step;
   - a `summary` naming the active repair depth, the repair-target `kind` and
     bounds you targeted, and what the next frontier/target reading should
     show if the hypothesis holds; depth-8 changes may be recorded only as
     acceptance accounting;
   - a `preview_observation` grounded only in what the preview
     identity's rendered variant let you see.
5. Submit through `submit_repair`. The supervisor uses your assessment as
   authored notes only.

## What assessment is not

- **Assessment cannot override measured facts.** The next intent
  response's `decision_facts` remains the sole source of truth for
  acceptance, residuals, and observable change. If your assessment
  claims a residual is fixed but `residual_summary.objective_facts`
  says otherwise, the objective facts win.
- **Assessment cannot rename or renumber steps.** `from_step` is bound to the
  selected parent's `decision_facts.step_ordinal`, while `to_step` is bound
  to the current Repair Attempt's intended child step; inventing other
  numbers is rejected.
- **Assessment cannot reference internal identifiers.** Do not paste
  handles, digests other than the preview identity, or paths outside
  `/candidate/work/`. Cite semantic facts, not identifiers.
- **Assessment cannot substitute for a submission.** Writing the
  file has no effect until you invoke `submit_repair` with the current
  handles.

## One worked example

Suppose the parent Measured Step response reported:

- `acceptance_state`: `unaccepted`
- `residual_summary.repair_frontier.active_depth`: `3`
- `repair_targets.items[0].kind`: `interior`
- `repair_targets.items[0].excess_surface_count`: `20`
- `repair_targets.items[0].bounds_canonical`: `min=[0.12, -0.08, 0.04]`,
  `max=[0.20, 0.08, 0.12]`
- `residual_summary.depth_8_surface_error_count`: `42` (exact accounting,
  not the edit selector)

You edit `work/source/model.py` to trim a boss you believe is
producing the top excess region, rebuild and preview, then write:

```json
{
  "schema": "mesh-to-cad.assessment/1",
  "from_step": 0,
  "to_step": 1,
  "preview_observation": "Preview shows the trimmed boss no longer overhangs the plate.",
  "summary": "Trimmed the boss diameter by 2 mm inside the depth-3 rank-0 excess target bounds. Expect that target's excess count or the depth-3 frontier error count to fall. Depth-8 counts remain acceptance accounting. Refuted if the same excess reappears in the same target bounds."
}
```

`submit_repair` then returns the next `decision_facts` object, and
those measured facts — not this assessment — decide whether the
hypothesis held.
