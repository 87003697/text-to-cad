# Review Agent contract

Use this contract inside the dedicated pilot-review sub-agent. Run Evidence
Compiler `prepare`, own the semantic draft, then run Evidence Compiler
`publish`. The compiler owns facts, authority validation, evidence-path
validation, protocol-check issuance, and publication. The Review Agent owns
protocol assessments, semantic verdicts, root-cause assignment, unresolved
questions, and the ordered fix playbook.

## Transaction

Keep one immutable source target and one review destination for the complete
transaction. Write only `review-input.json`, Review Agent drafts, and published
reports at the destination selected by `prepare`. Preserve the source, compiler
inputs, code, and snapshot byte-for-byte. Complete the transaction only when
`publish` exits zero and every experiment has both final reports. On failure,
return the phase and error without fabricating a report.

## Experiment draft

Write `<exp>/review-draft.json` under the destination selected by `prepare`.
With `--review-root`, this is `<review-root>/<exp>/review-draft.json`; evidence
paths still resolve against the immutable source experiment, not the review
destination.

```json
{
  "schema": "pilot-review.draft/2",
  "semantic_verdicts": {
    "reconstruction_quality": "accepted",
    "production_runtime_integration": "not_auditable"
  },
  "protocol_assessments": [
    {
      "check_id": "formal-preview-and-measurement",
      "status": "partial",
      "rationale": "The preview exists, but its measurement receipt is absent.",
      "evidence": [
        {
          "scope": "experiment",
          "path": "steps/000000/preview/preview.json"
        }
      ],
      "missing_evidence": "steps/000000/measurement.json"
    }
  ],
  "issues": [
    {
      "classification": "tool-interface-failure",
      "detail": "The registered build timed out after the configured budget.",
      "fix_target": "CAD canonical build exporter",
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

## Protocol assessments

Return exactly one assessment for every `protocol_checks[].check_id` in the
experiment `review-input.json`, preserving compiler order. The compiler rejects
missing, duplicate, and unknown IDs.

Allowed statuses:

- `observed`
- `partial`
- `missing`
- `not_applicable`
- `not_auditable`

Give every assessment a non-empty rationale and an evidence list. Cite at least
one existing evidence file for `observed`, `partial`, and `not_applicable`.
For `missing` and `not_auditable`, name `missing_evidence`; their evidence list
may be empty when no authority exists to cite.

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
      "fix_target": "CAD canonical build exporter",
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

Compare experiments only when their shipped snapshot, build interface, or other relevant
conditions are comparable. State the mismatch when they are not.

## Completion criterion

The transaction is complete when every experiment has both semantic verdicts,
exact protocol-check coverage, every finding has one root-cause owner and
verified evidence, unresolved symptoms name the cheapest discriminator, group
mode accounts for every experiment, `publish` exits zero, and all declared
reports exist.
