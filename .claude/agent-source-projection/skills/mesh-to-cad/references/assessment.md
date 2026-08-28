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
- `preview_observation` — a short human-language observation field retained
  by the closed schema. Ground it in returned Reference Observations, parent
  decision facts, the inspected formal preview, and the intended source edit;
  preview bytes and paths remain unavailable.
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
2. With the returned `preview_handle`, emit no text before calling the Agent
   Surface MCP inspection tool: code mode calls
   `mcp__agent_surface__inspect_formal_preview`; native Responses uses namespace
   `mcp__agent_surface` with child `inspect_formal_preview`. Never use a dotted
   server/tool spelling. Inspect its image block before creating or updating
   `/candidate/reconstruction-spec.json`, and record only visual facts actually
   visible there. Record semantic Components for the fuselage/body, left and
   right main wings, horizontal tailplane, and vertical fin with their canonical
   bounds; do not record paths, bytes, or handles.
3. Read `residual_summary.repair_frontier.active_depth` first. When it is
   non-null, use the active-depth interior targets and their
   `bounds_canonical` values to pick one residual you can plausibly move with
   a source edit. Also inspect any exterior targets on the same page: they
   are independent actionable residuals, not part of the interior frontier.
4. Make that edit under `/candidate/work/source/`. Do not touch any
   other file the supervisor named as its own.
5. After `run_candidate_tool` produces the measured candidate evidence, write
   `/candidate/work/assessment.json` with:
   - `from_step` copied from the selected parent's `decision_facts.step_ordinal`;
     `to_step` bound to the current Repair Attempt's intended child step;
   - a `summary` naming the active repair depth, the repair-target `kind` and
     bounds you targeted, and what the next frontier/target reading should
     show if the hypothesis holds; acceptance remains host-only;
   - a `preview_observation` grounded in the returned Reference Observation,
     parent decision facts, inspected formal preview, and intended source edit.
6. Submit through `submit_repair`. The supervisor uses your assessment as
   authored notes only.

## What assessment is not

- **Assessment cannot override measured facts.** The next intent
  response's `decision_facts` remains the sole source of truth for
  acceptance, residuals, and observable change. If your assessment
  claims a residual is fixed while the returned acceptance state remains
  unaccepted, the measured state wins.
- **Assessment cannot rename or renumber steps.** `from_step` is bound to the
  selected parent's `decision_facts.step_ordinal`, while `to_step` is bound
  to the current Repair Attempt's intended child step; inventing other
  numbers is rejected.
- **Assessment cannot reference internal identifiers.** Do not paste
  handles, digests, or paths outside
  `/candidate/work/`. Cite semantic facts, not identifiers.
- **Assessment cannot substitute for a submission.** Writing the
  file has no effect until you invoke `submit_repair` with the current
  handles.

## One worked example

Suppose the parent Measured Step response reported:

- `acceptance_state`: `unaccepted`
- `residual_summary.repair_frontier.active_depth`: `3`
- `repair_targets.items[0].kind`: `interior`
- `repair_targets.items[0].bounds_canonical`: `min=[0.12, -0.08, 0.04]`,
  `max=[0.20, 0.08, 0.12]`

You edit `work/source/model.py` to trim a boss you believe is
producing the top excess region, rebuild, then write:

```json
{
  "schema": "mesh-to-cad.assessment/1",
  "from_step": 0,
  "to_step": 1,
  "preview_observation": "The Reference Observation and depth-3 target bounds identify the boss region selected for trimming.",
  "summary": "Trimmed the boss diameter by 2 mm inside the depth-3 rank-0 target bounds. Expect the frontier error count to fall. Refuted if the same target bounds remain ranked after measurement."
}
```

`submit_repair` then returns the next `decision_facts` object, and
those measured facts — not this assessment — decide whether the
hypothesis held.
