---
name: pilot-review
description: >-
  Audit canonical mesh-to-cad Workspace pilots with deterministic evidence
  compilation and a local semantic Review Agent. Use for a pilot, output group,
  rollout failure, CAD-skill iteration, or CVM result analysis.
---

# pilot-review — Evidence Compiler + local Review Agent

## Outcome

Produce an evidence-backed audit of the complete canonical repair protocol,
not only the delivered model. Keep four verdicts separate:

1. runner completion;
2. Workspace protocol compliance;
3. reconstruction quality and declared limitations;
4. production runtime integration.

The two Modules have one seam:

```text
Evidence Compiler prepare → review-input.json
Local Review Agent         → review-draft.json
Evidence Compiler publish  → review.json / review.md / review-summary.md
```

The compiler owns deterministic facts and publication. The current local Agent
owns semantic interpretation. Do not dispatch another provider or reviewer.

## Input

Accept an experiment or group under `outputs/<group>/<exp>/`. Group mode skips
`_snapshot/` and accounts for every experiment child. Canonical authority is
only `workspace.json` with schema `mesh-to-cad.workspace/1`; unsupported or
legacy experiments remain explicitly classified rather than reconstructed from
filenames or telemetry.

## Workflow

### 1. Prepare deterministic evidence

Run from the repository root:

```bash
./.venv/bin/python .claude/skills/pilot-review/scripts/review.py prepare \
  outputs/<group> \
  --workspace-helper skills/mesh-to-cad/scripts/mesh-to-cad-workspace
```

The default and maximum validator budget is 1800 seconds. The compiler cancels
the validator process group on timeout and records `validator_timeout` with
Workspace protocol `not_auditable`; timeout never means invalid authority.

Preparation writes only:

- `<exp>/review-input.json` for every experiment;
- `<group>/review-input.json` in group mode.

Each experiment input freezes Workspace validation, graph, runner manifest,
artifact presence, immutable command records, bounded stderr previews, rollout
location, and shipped snapshot HEAD when available. Compiler identity digests
make later edits to deterministic baselines fail closed. Preparation is complete
when every discovered experiment appears in the group input, including failed
and unsupported experiments.

### 2. Perform the local semantic review

Read [`references/review-agent-contract.md`](references/review-agent-contract.md)
before writing drafts. Then inspect each `review-input.json` and only the raw
evidence needed to resolve its open questions.

Use this provenance matrix:

| Layer | Preferred evidence | Meaning |
|---|---|---|
| Prompted | user messages and injected skill blocks in `run/rollout.jsonl` | Instructions visible to the execution Agent |
| Shipped | `<group>/_snapshot/` and invoked installed-skill paths | Runtime intended to execute |
| Executed | tool calls/results, Workspace authority, and publishing commits | What happened |
| Current | current source checkout | Forward-looking comparison only |

Treat `run/`, `work/`, logs, rollout, traces, and transfer manifests as
telemetry. They can explain authority but cannot redefine it. Pair tool calls
with results by call ID and record order. Agent prose is a claim until a tool
result or authority artifact confirms it.

For every expected graph edge, determine `observed`, `partial`, `missing`,
`not_applicable`, or `not_auditable`. Locate the last good node and first
failing node. Compare sibling Attempts before assigning a root cause. Missing
evidence stays missing.

### 3. Write Review Agent drafts

Write:

- `<exp>/review-draft.json` for every experiment;
- `<group>/review-summary-draft.json` in group mode.

The draft supplies only semantic verdicts, findings, unresolved questions,
evidence gaps, and the ordered fix playbook. Runner and Workspace verdicts come
from the compiler and cannot be overridden. Every finding has one primary root
cause, a concrete fix target, and at least one existing evidence file.

Drafting is complete when every experiment listed by the compiler has a draft,
all plausible competitors and failed Attempts were considered, and every
unresolved symptom names the cheapest discriminating next experiment.

### 4. Validate and publish

Run:

```bash
./.venv/bin/python .claude/skills/pilot-review/scripts/review.py publish \
  outputs/<group>
```

The compiler validates all drafts before publishing any final report. It
rejects unknown root-cause classes, invalid semantic verdicts, missing evidence
files, path escapes, altered compiler inputs, and incomplete group coverage. On
success it writes only:

- `<exp>/review.md`;
- `<exp>/review.json`;
- `<group>/review-summary.md` in group mode.

Publication is complete only when the command exits zero and every discovered
experiment has both final report files.

## Canonical graph expectations

```text
Canonical Reference + setup
  → Workspace init
  → Attempt for Measured Step 0
  → formal preview + measurement
  → Measured Step 0
  → Repair Batch → Attempt → Region Diff → Measured Step + Repair Cycle
  → at most five successful Repair Cycles
  → final selection
  → isolated registered rebuild
  → provenance validation + non-publishing verification + final preview
  → atomic Final Delivery
```

Failed Attempts are side nodes and consume no Repair Cycle. A measured
geometric no-op consumes one. A child may branch from any earlier Measured
Step; explicit ancestry is authoritative.

## Guardrails

- Support only `mesh-to-cad.workspace/1` as experiment authority.
- Preserve process, result, runtime, and runner evidence as distinct claims.
- Keep the experiment immutable except for review inputs, drafts, and reports.
- Use finalized SQLite traces with `immutable=1` when trace inspection is
  necessary.
- Keep the workflow local and read-only: no network request, model dispatch,
  dependency installation, viewer, CAD rebuild, or experiment repair.
- The legacy `review.py <exp>` interface remains compatibility-only; new audits
  use `prepare` and `publish`.
