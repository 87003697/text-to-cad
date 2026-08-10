---
name: pilot-review
description: >-
  Audit canonical mesh-to-cad Workspace pilots, reconstruct their immutable
  graph and evidence chain, and emit review.md/review.json. Trigger:
  "pilot-review", "审阅 pilot", "看 outputs", "分析 rollout",
  "agent 为什么没解决", "iterate CAD skill", "cvm 运行结果".
---

# pilot-review — canonical Workspace audit

## Purpose

Audit the complete canonical repair protocol, not only the delivered model.
Keep these verdicts separate:

1. runner completion;
2. Workspace protocol compliance;
3. reconstruction quality and declared limitations;
4. production runtime integration.

This skill is read-only except for its own review artifacts. It never repairs,
migrates, or reinterprets experiment state.

## Supported input

Interpret the argument as an experiment directory, group directory, or glob
under `outputs/<group>/<exp>/`. Skip `_snapshot/` when reviewing a group.

A supported experiment contains `workspace.json` with schema
`mesh-to-cad.workspace/1`. If that authority is absent or a pre-cutover layout
is detected, report `unsupported_legacy_workspace` and stop Workspace analysis
for that experiment. Do not reconstruct another protocol from filenames,
commits, notes, or runner telemetry.

## Expected graph

Reconstruct the immutable directed graph:

```text
Canonical Reference + setup
  → Workspace init
  → Attempt for Measured Step 0
  → formal preview + measurement
  → Measured Step 0
  → Repair Batch
  → Attempt
  → child preview + measurement + Region Diff
  → Measured Step + Repair Cycle
  → ... at most five successful Repair Cycles
  → final selection of a Selected Step
  → isolated registered rebuild
  → provenance validation + non-publishing verification + final preview
  → atomic Final Delivery
```

Failed Attempts are side nodes and consume no Repair Cycle. A measured
geometric no-op does consume one. A child may branch from any earlier Measured
Step; always read explicit ancestry.

## Audit workflow

### 1. Freeze evidence sources

Build a provenance matrix before judging behavior:

| Layer | Preferred evidence | Meaning |
|---|---|---|
| Prompted | user messages and injected skill blocks in `run/rollout.jsonl` | Instructions visible to the Agent |
| Shipped | `<group>/_snapshot/` and invoked installed-skill paths | Runtime intended to execute |
| Executed | tool calls, outputs, Workspace artifacts, and Git commits | What actually happened |
| Current | current source checkout | Forward-looking comparison only |

Use historical prompted/shipped evidence to judge Agent policy, but require the
canonical Workspace schemas for experiment authority. Contract drift is a
separate finding; current source is not retroactive law.

### 2. Validate authority first

- Run `./.venv/bin/python .claude/skills/pilot-review/scripts/review.py <exp>
  --workspace-helper skills/mesh-to-cad/scripts/mesh-to-cad-workspace` first.
  It invokes the Workspace skill's public `validate` process interface
  read-only and publishes only `review.md` and `review.json`.
- Confirm `workspace.json`, `experiment.json`, Canonical Reference identities,
  setup identity, `step_index.json`, immutable steps/cycles/attempts, and their
  publishing commits agree.
- Confirm at most five successful Repair Cycles, bounded Attempts/tool
  failures, explicit ancestry, marker-last recovery state, and LFS evidence.
- Treat `run/`, `work/`, runner logs, rollout, trace, and transfer manifests as
  telemetry. They cannot redefine Workspace facts.

If validation fails, report the exact classification and continue only with
checks whose authority remains independently readable.

### 3. Build the execution evidence index

Read large rollout evidence selectively. Pair tool calls with results by call
ID and record order, arguments, working directory, exit status, bounded output,
and the next dependent Agent decision. Treat Agent prose as a claim until a
tool result or authority artifact confirms it.

Use `artifact_manifest.json` for runner publication state and
`scripts/utils/rollout-usage.py <exp>/run/rollout.jsonl` for cost. Trace artifacts
are optional cross-checks, never authority substitutes.

### 4. Audit each graph edge

For every expected node record `observed`, `partial`, `missing`,
`not_applicable`, or `not_auditable`, plus exact evidence and incoming/outgoing
edges. Check:

- Canonical Reference was prepared once and candidates stayed in its frame.
- Each Attempt froze the correct initial plan or Repair Batch before execution.
- Candidate source, registered recipe, artifacts, formal preview, and
  measurement belong to the same identity.
- Every nonzero Measured Step, Repair Cycle, Region Diff, assessment, and
  source-change record names the same explicit parent.
- Repair Target selection and Planned Edits are complete and auditable.
- Agent assessment cites observed preview and Region Diff evidence without
  rewriting objective facts.
- Stop decisions respect objective acceptance and the bounded cycle budget.
- Final selection considered plausible competitors and preserved the Selected
  Step acceptance state.
- Final Delivery was rebuilt from archived source in isolated offline staging,
  has complete provenance, matches Selected Step Observable Geometry, and was
  published atomically.

### 5. Assign one root-cause owner

Use exactly one primary class per finding:

| Root cause | Test | Typical fix target |
|---|---|---|
| `agent-policy-deviation` | Contract and evidence were clear; Agent chose another action | Owning skill guardrail or evaluation |
| `contract-gap` | Required information was absent or undiscoverable | Owning skill/reference |
| `contract-ambiguity` | Governing instructions conflict | Both conflicting sections |
| `tool-interface-failure` | Correct call failed or violated its contract | Tool/package function |
| `runtime-deployment-failure` | Source, bundle, launcher, or dependency differed | Bundle/push/runner |
| `observability-gap` | Required behavior cannot be proven | Runner/Workspace evidence publication |
| `modeling-limit` | Protocol was correct but the representation could not close the residual | Route/modeling strategy |

Every finding names a concrete file/section or function. For an unresolved
symptom, record the last good node, first failing node, observed evidence,
missing evidence, and cheapest discriminating next experiment.

## Required report

Write only:

- `<exp>/review.md`
- `<exp>/review.json`
- `outputs/<group>/review-summary.md` for group review

`review.md` contains the four verdicts, provenance matrix, expected-vs-actual
graph, Workspace validation result, findings grouped by root cause, unresolved
problems, and an ordered fix playbook.

`review.json` contains:

```json
{
  "verdicts": {},
  "contract_provenance": {},
  "workspace_validation": {},
  "graph": {"nodes": [], "edges": []},
  "issues": [],
  "unresolved": [],
  "evidence_gaps": []
}
```

## Non-negotiables

- Support only `mesh-to-cad.workspace/1` experiment authority.
- Keep process, result, runtime, and runner evidence separate.
- Missing evidence is `not_auditable`, never a guessed pass.
- Do not issue network requests, call a model, install dependencies, start a
  viewer, or alter experiment artifacts beyond the review outputs.
- Run every applicable check for every experiment in group mode.
