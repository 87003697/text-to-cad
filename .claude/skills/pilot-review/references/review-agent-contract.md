# Review Agent contract

Read this after Evidence Compiler `prepare` succeeds. The compiler owns facts,
authority validation, evidence-path validation, and publication. The Review
Agent owns semantic verdicts, root-cause assignment, unresolved questions, and
the ordered fix playbook.

## Experiment draft

Write `<exp>/review-draft.json` under the destination selected by `prepare`.
With `--review-root`, this is `<review-root>/<exp>/review-draft.json`; evidence
paths still resolve against the immutable source experiment, not the review
destination.

```json
{
  "schema": "pilot-review.draft/1",
  "semantic_verdicts": {
    "reconstruction_quality": "accepted",
    "production_runtime_integration": "not_auditable"
  },
  "issues": [
    {
      "classification": "tool-interface-failure",
      "detail": "The registered build timed out after the configured budget.",
      "fix_target": "implicit-cad canonical build exporter",
      "evidence": [
        {
          "scope": "experiment",
          "path": "attempts/000001/commands/000001/command.json",
          "selector": "duration_ms=900154"
        }
      ],
      "last_good_node": "attempt:1",
      "first_failing_node": "canonical candidate build",
      "missing_evidence": "exporter phase progress",
      "cheapest_next_experiment": "Run the archived source provider-free with phase timing."
    }
  ],
  "unresolved": [
    "The evidence does not distinguish slow completion from non-termination."
  ],
  "evidence_gaps": [
    "The failed source is not archived as Workspace authority."
  ],
  "fix_playbook": [
    "Add exporter phase telemetry.",
    "Run the cheapest provider-free discriminator."
  ]
}
```

Allowed reconstruction verdicts:

- `accepted`
- `delivered_with_residual`
- `failed_before_measurement`
- `not_auditable`

Allowed production runtime verdicts:

- `pass`
- `fail`
- `not_auditable`

The compiler preserves `runner_completion` and `workspace_protocol` from
deterministic evidence. The draft cannot override them.

Every issue has exactly one `classification`:

- `agent-policy-deviation`
- `contract-gap`
- `contract-ambiguity`
- `tool-interface-failure`
- `runtime-deployment-failure`
- `observability-gap`
- `modeling-limit`

Every issue requires a concrete `fix_target` and at least one existing evidence
file. Evidence `scope` is `experiment` for paths under the current experiment
or `group` for paths under the group, including `_snapshot/`. `selector` is an
optional record index, call ID, JSON field, line, or other precise locator.

## Group draft

For group input, also write `<review-root>/review-summary-draft.json` (or the
source group path when using compatible in-place mode):

```json
{
  "schema": "pilot-review.group-draft/1",
  "summary": "One experiment delivered with residual while one failed in the exporter.",
  "cross_experiment_findings": [
    {
      "classification": "tool-interface-failure",
      "detail": "Only the ring-heavy source exhausted the build budget.",
      "fix_target": "implicit-cad canonical build exporter",
      "evidence": [
        {
          "scope": "group",
          "path": "bicycle/attempts/000001/commands/000001/command.json"
        }
      ]
    }
  ],
  "fix_playbook": [
    "Profile a minimal ring and the failed source before another paid run."
  ]
}
```

Compare experiments only when their shipped snapshot, route, or other relevant
conditions are comparable. State the mismatch when they are not.

## Completion criterion

The draft is complete when every experiment has both semantic verdicts, every
finding has one root-cause owner and verified evidence, unresolved symptoms
name the cheapest discriminator, and group mode accounts for every experiment
listed in the group `review-input.json`.
