---
name: pilot-review
description: >-
  Audit canonical mesh-to-cad Workspace pilots through one dedicated sub-agent
  running deterministic evidence compilation and semantic review. Use for a
  pilot, output group, rollout failure, CAD-skill iteration, or CVM result analysis.
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

The compiler owns deterministic facts and publication. The Review Agent owns
semantic interpretation. Keep them as two Modules inside one dedicated
sub-agent workflow.

## Execution role

When invoking this skill as the caller, dispatch exactly one stable sub-agent
for the complete review transaction. Give it the repository root, immutable
source target, review root, and Workspace helper. Resume that same sub-agent for
follow-ups. The caller performs no `prepare`, drafting, `publish`, or semantic
interpretation.

When assigned as that dedicated sub-agent, execute every Workflow step locally
and spawn no further reviewer. Write only the declared review destination.
Return the source target, review root, compiler status, per-experiment verdicts,
report paths, phase timings, and unresolved questions. If sub-agent dispatch is
unavailable to the caller, stop before preparation and report the missing seam.

## Input

Accept an experiment or group under `outputs/<group>/<exp>/`. Group mode skips
`_snapshot/` and accounts for every experiment child. Canonical authority is
only `workspace.json` with schema `mesh-to-cad.workspace/1`; unsupported or
legacy experiments remain explicitly classified rather than reconstructed from
filenames or telemetry.

## Workflow for the dedicated sub-agent

### 1. Prepare deterministic evidence

Run from the repository root:

```bash
./.venv/bin/python .claude/skills/pilot-review/scripts/review.py prepare \
  outputs/<group> \
  --workspace-helper skills/mesh-to-cad/scripts/mesh-to-cad-workspace
```

For mounted evidence or a non-destructive rerun, separate the immutable source
from the writable review destination:

```bash
./.venv/bin/python .claude/skills/pilot-review/scripts/review.py prepare \
  outputs/<group> --review-root /tmp/pilot-reviews/<group> \
  --workspace-helper skills/mesh-to-cad/scripts/mesh-to-cad-workspace
```

`--review-root` preserves the group layout but writes every review artifact
under that root. The compiler seals the resolved source group and experiment
paths, so publishing against another same-named source fails closed. Prefer
this mode for rclone/S3 mounts; read authority in place instead of copying the
experiment. Keep the review root disjoint from the source tree; overlapping
paths fail before any review artifact is written.

The default and maximum validator budget is 1800 seconds. The compiler cancels
the validator process group on timeout and records `validator_timeout` with
Workspace protocol `not_auditable`; timeout never means invalid authority.

Preparation writes only:

- `<exp>/review-input.json` for every experiment;
- `<group>/review-input.json` in group mode.

With `--review-root`, these paths are relative to the review root and the source
experiment receives no writes.

Each experiment input freezes Workspace validation, graph, protocol checks,
runner manifest, artifact presence, immutable command records, bounded stderr
previews, rollout location, and shipped snapshot HEAD when available. Compiler
identity digests make later edits to deterministic baselines fail closed.
Preparation is complete when every discovered experiment appears in the group
input, including failed and unsupported experiments.

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

Assess every compiler-issued `protocol_checks` entry as `observed`, `partial`,
`missing`, `not_applicable`, or `not_auditable`. Locate the last good node and
first failing node. Compare sibling Attempts before assigning a root cause.
Missing evidence stays missing.

Before drafting, reconstruct the Measured Step decision chain in review notes.
Use the contract's evidence, attribution, Active Depth, and cross-pilot
comparison rules; cite raw evidence rather than inferring private Agent
reasoning.

### 3. Write Review Agent drafts

Write:

- `<exp>/review-draft.json` for every experiment;
- `<group>/review-summary-draft.json` in group mode.

When preparation used `--review-root`, write drafts under the same review root.

The draft supplies protocol assessments, semantic verdicts, findings,
unresolved questions, evidence gaps, and the ordered fix playbook. Use the
reconstructed Step chain to make each finding explain a decision transition,
rather than summarizing final verdicts or graph counts. Runner and Workspace
verdicts come from the compiler and cannot be overridden. Every finding has one
primary root cause, a concrete fix target, and at least one existing evidence
file.

Drafting is complete when every experiment listed by the compiler has a draft,
its assessments exactly cover the issued protocol check IDs, all plausible
competitors and failed Attempts were considered, and every unresolved symptom
names the cheapest discriminating next experiment.

### 4. Validate and publish

Run:

```bash
./.venv/bin/python .claude/skills/pilot-review/scripts/review.py publish \
  outputs/<group> --review-root /tmp/pilot-reviews/<group>
```

Pass the same source target and review root used by `prepare`. Omit
`--review-root` only for the compatible in-place workflow.

The compiler validates all drafts before publishing any final report. It
rejects missing, duplicate, or unknown protocol check IDs, unknown root-cause
classes, invalid semantic verdicts, missing evidence files, path escapes,
altered compiler inputs, and incomplete group coverage. On success it writes
only:

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
  → final selection of a Selected Step
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
- Prefer an external review root when the evidence source is mounted, shared,
  or already has reports that must remain unchanged.
- Use finalized SQLite traces with `immutable=1` when trace inspection is
  necessary.
- Keep the workflow local and read-only: no network request, external or paid
  model dispatch, dependency installation, viewer, CAD rebuild, or experiment
  repair.
- Keep the dedicated sub-agent on the complete transaction; resume it instead
  of replacing it or letting the caller author missing drafts.
- The legacy `review.py <exp>` interface remains compatibility-only; new audits
  use `prepare` and `publish`.
