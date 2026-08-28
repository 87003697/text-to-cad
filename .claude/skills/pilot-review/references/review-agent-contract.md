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

## Measured Step review method

Before authoring the existing draft fields, reconstruct the complete ordered
Measured Step chain in working notes. For every Step and parent, record the
Agent-visible observation or explicit evidence gap, the public hypothesis or
plan, the actual tool/source change, the parent-relative measured geometry
result, Active Depth missing/excess/total, and the outcome or evidence gap that
led to the next decision. Cite raw evidence; do not infer unrecorded Agent
mental state.

Keep source change, geometry result, and evaluator result separate. The
parent-relative geometry result is `changed`, `no-op`, or an evidence gap. A
restoration or rollback target is a separate optional note: call it a rollback
only when evidence from an earlier Step establishes the restored target. Do
not use it as a substitute for the parent-relative geometry result.

A preview receipt proves only that the receipt exists. It does not establish
that a preview artifact is readable, that pixels exist, or that the execution
Agent inspected it. Attribute visual claims to the execution Agent only with
execution evidence; reviewer visual observation must be explicitly attributed
to the reviewer.

For each Step, take Active Depth and its missing/excess/surface-error counts
from that Step's decision or evaluation facts at
`residual_summary.repair_frontier`. Use this frontier to explain repair choice.
Use Depth-8 only for final acceptance and residual accounting. When the review
can only derive a depth from `measurement.errors_by_depth`, label it a reviewer
derivation and an evidence gap; never substitute Depth-8 for the frontier.

Before a cross-pilot conclusion, verify every result-affecting condition that
is relevant to the conclusion, including the shipped snapshot, build interface,
and any differing inputs, runtime, evaluator, or policy conditions. If a
condition is missing or differs, state the mismatch and limit the conclusion to
an association; do not make a single-variable causal attribution.

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

The compiler preserves `workspace_protocol` from the verified Terminal
Validation Result. The draft cannot override it.

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

## Completion criterion

The transaction is complete when every experiment has both semantic verdicts,
exact protocol-check coverage, every finding has one root-cause owner and
verified evidence, unresolved symptoms name the cheapest discriminator, group
mode accounts for every experiment, `publish` exits zero, and all declared
reports exist.
