---
name: pilot-review
description: >-
  Audit mesh-to-cad pilot experiments from contract through agent tool
  execution, decision branches, artifacts, runtime integration, and cost.
  Reconstruct the unrolled workflow graph from rollout.jsonl and emit
  review.md/review.json with root-cause-specific fix targets. Trigger:
  "pilot-review", "审阅 pilot", "看 outputs", "分析 rollout",
  "agent 为什么没解决", "iterate CAD skill", "cvm 运行结果".
---

# pilot-review — Agent execution and result audit

## Purpose

Audit more than the final CAD files. Answer five separate questions:

1. Did the Agent receive a complete, internally consistent contract?
2. Did it invoke the correct skills/tools with the required arguments?
3. Did every decision follow from evidence observed before that decision?
4. Are the final artifacts and claims valid?
5. If the task remained unresolved, which layer owns the root cause?

Never collapse these questions into one pass/fail result. A runner may exit
`0` while reconstruction quality plateaus, and a good artifact may be produced
through a non-production runtime override.

## Workflow model

Treat the reconstruction loop as an **unrolled directed acyclic graph**:

```text
contract/input
  → inspect → route
  → model_0 → measure_0 → decide_0
      ├─ refine → model_1 → measure_1 → decide_1 → ...
      └─ accept | plateau | divergence
           → visual_verify → notes → final_handoff
```

Skill/reference loads, commits, viewer startup/health checks, and runner
publication are sidecar nodes attached to the node whose contract requires
them. Each iteration is a new node set; do not represent the loop as an
uninspectable cycle.

For every edge `A → B`, require evidence that A completed and its relevant
output was observed before B's dependent decision. Final file presence alone
does not prove that the required process ran.

## Audit workflow

### 1. Resolve experiments

Interpret `$ARGUMENTS` as an exp dir, group dir, or glob.

- Layout: `outputs/<group>/<exp>/`.
- Group input: audit every exp child; skip `_snapshot/`.
- Empty input: select the newest `outputs/*/*/` exp.
- If the directory is not a mesh-to-cad experiment, emit a scoped warning and
  skip mesh-specific checks rather than crashing.

### 2. Freeze the historical contract

Build a provenance matrix before judging behavior:

| Contract layer | Preferred evidence | Meaning |
|---|---|---|
| Prompted | user messages and injected `<skill>` blocks in `rollout.jsonl` | Exact instructions visible to the Agent |
| Shipped | `<group>/_snapshot/` and invoked `/home/pilot/.codex/skills/...` paths | Product/runtime code intended to run |
| Executed | function calls, command outputs, and generated artifacts | What actually ran |
| Current | current checkout `skills/`, `packages/`, and references | Forward-looking comparison only |

Rules:

- Judge historical compliance against the **prompted historical contract**.
  Use `_snapshot/` to inspect the corresponding implementation when present.
- Never silently apply today's thresholds or workflow to an older pilot.
- If prompted, shipped, and current contracts differ, report
  `contract-provenance-drift`; state which contract governed the verdict.
- If neither rollout contract nor snapshot is available, mark process checks
  `not_auditable`; artifact checks may still run.
- Detect contradictions inside the same historical contract. An Agent choosing
  one of two conflicting instructions is a `contract-ambiguity`, not
  automatically an Agent-policy failure.

### 3. Build an evidence index

Read large evidence selectively; do not dump the full rollout into context.

- Pair `response_item.function_call` with
  `response_item.function_call_output` by `call_id`.
- Record timestamp/order, tool name, command or arguments, `workdir`, exit
  status, durable outputs, and the next Agent message or decision.
- Treat Agent messages/reasoning as claims. Confirm them with tool output or
  artifacts before using them as facts.
- Read `artifact_manifest.json` for publication status and file inventory.
- Read `.git/` when present. Otherwise recover `git log --oneline` and relevant
  `git show` output from rollout. If neither exists, mark trajectory
  `not_auditable`.
- Use `scripts/utils/rollout-usage.py <exp>/rollout.jsonl` as the new-pilot
  cost source; only old experiments without rollout may fall back to
  `usage.json`.
- `trace.html` and `traces.sqlite3` are optional cross-checks, not substitutes
  for rollout or artifacts.

### 4. Reconstruct and align the graphs

Derive the expected graph from the historical orchestrator contract and the
peer skills/references it required the Agent to load. Derive the actual graph
from the evidence index.

For each expected node record:

- `status`: `observed`, `partial`, `missing`, `not_applicable`, or
  `not_auditable`;
- exact evidence location;
- required inputs and produced outputs;
- selected outgoing branch;
- whether a dependent decision occurred only after its evidence was observed.

Flag:

- missing required nodes;
- wrong ordering or skipped observation edges;
- unexpected detours that modify product/runtime behavior;
- repeated retries without a new diagnosis;
- claims of tool success unsupported by exit status/output;
- final claims unsupported by the graph.

### 5. Audit five logical modules

#### A. Contract and context sufficiency

- Did the Agent receive the orchestrator, triggered peer skill, and required
  references before the corresponding action?
- Were paths, filenames, thresholds, tool arguments, runtime assumptions, and
  exit semantics discoverable?
- Did contracts contradict one another or reference unavailable commands?
- Did shipped skill/runtime content differ from the prompted contract?

#### B. Execution graph and tool use

- Did inspect, route, modeling, measurement, visual verification, and
  finalization use the owning skill/tool?
- Were command arguments, `workdir`, sample/seed, iteration number, bounds,
  timeout, and output paths contract-compliant?
- Did each call finish, and did the Agent consume its actual output?
- Did launcher/viewer health checks prove the service was usable rather than
  merely spawned?

#### C. Decision and branch correctness

- Replay routing from historical `routing-rubric.md`.
- Replay accept/refine/plateau/divergence from the historical thresholds and
  each iteration's metrics.
- Confirm a refine step uses the permitted diagnosis source. When the contract
  says to consume one `voxblame.next_action`, compare its world-space bounds
  and direction with the subsequent localized code change.
- Confirm divergence preserves the intended prior candidate and records the
  terminal state according to the governing historical convention.
- Do not use IoU, Hausdorff, or another metric as a hard gate unless that
  historical contract made it one.

#### D. Result, artifact, and integration integrity

Retain the product checks, but attach each to its graph node:

- `notes.md` headings and fields match the historical schema.
- `route.json` includes a non-empty rejected alternative.
- Output naming follows the historical rules.
- Required primary model, exported mesh, metrics, previews, reviews, and notes
  exist and agree with final claims.
- Repeated structural classes are preserved or explicitly omitted.
- Accepted results replay all historical hard gates. High Hausdorff without
  the required diagnosis remains a warning when Hausdorff is warning-only.
- `artifact_manifest.json` exists, is valid, and has integer
  `final_status`. Report runner completion separately from reconstruction
  verdict and production integration.
- Detect top-level `sitecustomize.py`, `.runtime/`, or other shims that replace
  product skill/runtime behavior. Runner exit `0` cannot turn such a run into
  a production-integration pass.

#### E. Efficiency, resilience, and unresolved problems

- Baseline cost is USD 0.30 per pilot: above 2× is warn; above 5× is error.
  Report cache hit rate when available.
- Identify long-running calls, timeout/retry sequences, redundant tool starts,
  repeated full-file reads, and expensive work discarded without learning.
- For every unresolved symptom, name the last good graph node, first failing
  node, observed evidence, missing evidence, and the cheapest discriminating
  next experiment.

### 6. Assign root cause and fix target

Classify every issue into exactly one primary owner:

| Root cause | Test | Typical fix target |
|---|---|---|
| `agent-policy-deviation` | Contract was clear, required evidence existed, Agent chose another action | Owning product `SKILL.md` guardrail or evaluation |
| `contract-gap` | Required information was absent or undiscoverable | Owning `SKILL.md` / reference |
| `contract-ambiguity` | Two governing instructions conflict | Both conflicting sections |
| `tool-interface-failure` | Correct invocation failed or output violated its documented contract | Tool script/package function |
| `runtime-deployment-failure` | Source/bundle/launcher/runtime mismatch or unavailable dependency | Bundle, push, launcher, or runner |
| `observability-gap` | Behavior cannot be proven from retained evidence | Runner trace/manifest/rollout publication |
| `modeling-limit` | Process was correct but quality plateaued on the chosen representation | Modeling skill strategy or routing rubric |

Every issue must include a concrete fix target: `<file> § <section>` or
`<file>::<function>`. Do not use generic advice such as "improve the prompt".

## Required report

Write only review artifacts; never repair the experiment.

### Per experiment

- `<exp>/review.md`
- `<exp>/review.json`

`review.md` must contain:

1. **Four-dimensional verdict**
   - runner completion;
   - workflow compliance;
   - reconstruction quality;
   - production integration.
2. **Contract provenance matrix** and any drift/contradiction.
3. **Expected vs actual graph** table with node status and evidence.
4. **Issues grouped by root cause**, then severity.
5. **Unresolved-problem table**:
   symptom, last good node, failing node, likely cause, evidence gap, next
   experiment.
6. **Fix playbook** ordered by highest-leverage upstream cause.

`review.json` must include:

```json
{
  "verdicts": {},
  "contract_provenance": {},
  "graph": {"nodes": [], "edges": []},
  "issues": [],
  "unresolved": [],
  "evidence_gaps": []
}
```

### Group review

Also write `outputs/<group>/review-summary.md`. Aggregate by graph node,
root-cause class, and issue signature. Distinguish systemic contract/tool
failures from object-specific modeling limits.

Stdout should show the first five errors, report paths, and the highest-leverage
next fix.

## Non-negotiable

- Historical prompted contract first; current checkout is not retroactive law.
- Process evidence, result evidence, and runtime evidence remain separate.
- Exit `0`, artifact existence, and metric acceptance are three different facts.
- Missing evidence produces `not_auditable`, never a guessed pass.
- Heuristic preservation checks are warn-only.
- Do not issue network requests, call a model, install dependencies, or start
  the viewer while reviewing.
- Do not modify exp artifacts other than `review.md` and `review.json`.
- Run every applicable check for every exp; no sampling in group mode.

## Maintenance

Update this skill when a new failure cannot be represented as a graph-node,
edge, evidence, or root-cause issue. Prefer extending the governing product
contract or deterministic evidence publication over adding another isolated
final-file heuristic.
