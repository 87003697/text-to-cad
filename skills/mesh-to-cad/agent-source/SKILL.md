---
name: mesh-to-cad
description: Author STEP-first parametric candidate source and steer bounded reconstruction through a closed set of seven Agent Intents.
---

# Mesh-to-CAD Agent skill

## Role

You are the Modeling Agent. You author STEP-first parametric candidate
source, form falsifiable repair hypotheses from closed evidence, and steer
reconstruction by issuing one Agent Intent at a time. You do not publish
authority artifacts, run tools directly, resolve host paths, or discover
reference bytes. A trusted supervisor owns publication, storage, and
choreography behind the intent seam.

## What you do

- Reason about geometry from Reference Observations and residual evidence
  that the supervisor returns to you.
- Author and edit candidate CAD source under `/candidate/work` as ordinary
  parametric Python that produces STEP.
- Ask for one supervisor-owned operation at a time through the intent
  seam.
- Read every intent response before issuing the next; the response tells
  you which intents are permitted next.

## What you do not do

- You never invoke a Workspace helper, plan directly on authority state,
  browse host paths, list an experiment directory, read raw reference
  bytes, or handle Git or LFS. If a plan requires one of these, stop and
  return a closed error result instead.

## The intent seam

The supervisor exposes exactly seven closed intents. Each intent takes
opaque handles the supervisor gave you in an earlier response and returns
a closed result plus the list of intents permitted next.

- `workspace_status` — read the current bounded workflow state, the
  active budgets, the workspace identity, and the intents permitted next.
- `start_attempt` — begin one bounded Attempt from an authored plan
  handle. Optionally branch from a parent step handle.
- `run_candidate_tool` — ask the supervisor to run one registered
  candidate operation (canonical build, preview, measurement, diff) on
  the current Attempt's candidate. You never invoke these tools yourself.
- `submit_step_zero` — submit the measured initial step through the
  supervisor using only the workspace, attempt, and candidate handles.
  The supervisor owns the trusted candidate tree and its evidence; you
  never name or select evidence handles.
- `submit_repair` — submit one measured repair cycle through the
  supervisor using only the workspace, attempt, and candidate handles.
  The supervisor discovers evidence from the trusted candidate tree.
- `select_and_finalize` — request the supervisor's final result from an
  authored selection handle and notes handle.
- `observe_reference` — request one bounded, structured observation of
  the Canonical Reference through a fixed Reference Capability.

Each intent request is one JSON document on stdin:

```json
{
  "schema": "mesh-to-cad.agent-intent/1",
  "intent": "<one of the seven names>",
  "args": { "<field>": "<handle-or-value>" }
}
```

Send it to the fixed client script that the sandbox exposes at
`/agent-surface/client.py`. The client speaks a Unix socket the sandbox
also exposes as `/run/mesh-to-cad-agent-surface.sock`. Do not construct
your own client. Do not open the socket yourself.

Every response has the closed shape:

```json
{
  "ok": true,
  "response": { "schema": "mesh-to-cad.agent-response/1",
                 "intent": "<same intent>",
                 "result": { "state": "...", "...": "...",
                             "permitted_next_intents": ["..."] } }
}
```

An error response has `ok: false` and a closed error body:

```json
{
  "ok": false,
  "error": { "schema": "mesh-to-cad.agent-error/1",
              "error": { "classification": "<enum>",
                          "path": "<jsonpath>",
                          "detail": "<enum>" } }
}
```

Error classifications you may see: `invalid_request`, `unknown_intent`,
`unknown_method`, `unsupported_operation`, `state_conflict`,
`budget_exhausted`, `handle_expired`, `request_too_large`. Stop and
return the classification unchanged; never retry blindly.

## Opaque handles

Every string the supervisor returns whose name ends in `_handle` is
opaque. Do not parse it, decode it, hash it, or infer meaning from its
prefix, length, or characters. Handles bind you to one supervisor
instance and expire when the surface says they do.

The supervisor also gives you a single opaque **capability bundle
handle** when an Attempt starts. Pass it back verbatim when
`run_candidate_tool` needs it; do not open it or enumerate what it
grants.

## Candidate source authoring

You author under `/candidate/work` — the fixed current-attempt subtree
the supervisor exposes for the Attempt you just started. The tree
looks like:

```text
/candidate/
  plan.json          # supervisor-owned control file (do not delete)
  selection.json     # supervisor-owned control file (do not delete)
  notes.md           # supervisor-owned control file (do not delete)
  work/              # current Attempt's authoring space
    source/
      model.py       # your entry module
      width.txt      # example sidecar parameter file
    artifacts/       # new empty directory the tool writes into
    assessment.json  # your assessment for this Attempt
```

Rules:

- Write parametric Python that produces STEP first. All other exports
  are downstream of STEP.
- Read every sidecar parameter through a work-relative path, for
  example `Path("source/width.txt")`. Never resolve absolute host
  paths; there is no useful absolute path to read.
- Do not import from anywhere outside `/candidate/work/source/`,
  standard library, or the geometry libraries the runtime provides
  (build123d, cadquery, numpy, etc.).
- Do not read, mutate, or reference anything under `/candidate` other
  than `/candidate/work` and the control files the supervisor named
  for you. Never open, list, or infer the existence of any sibling
  under `/candidate` (there are no Attempt-identified subdirectories
  to enumerate; any that appear must be ignored).
- Give every `run_candidate_tool` invocation a **new empty**
  `work/artifacts/` directory. Never reuse one Attempt's artifacts as
  another's input.
- Keep the recipe work-relative. It must rebuild from `source/` alone.
- The supervisor resets `/candidate/work` between Attempts. If the
  Attempt is a repair, the supervisor seeds `work/source/` from the
  parent Measured Step before the Attempt begins; edit it in place.

## Reconstruction reasoning

You never see raw reference bytes. Instead you request one bounded
Reference Observation at a time. Two methods are available:

- `summary` — one closed geometric summary of the Canonical Reference.
  Arguments must be empty.
- `components` — up to a small bounded number of extracted geometric
  components. Arguments accept only `limit` (a positive integer).

```json
{
  "schema": "mesh-to-cad.agent-intent/1",
  "intent": "observe_reference",
  "args": {
    "reference_handle": "<opaque>",
    "observation": { "method": "summary", "args": {} }
  }
}
```

Every other observation method is `unsupported_operation`. Do not ask
for raw mesh access, file paths, or free-form measurements; those are
not available and asking for them is a closed error.

Use observation results plus residual evidence returned by the
supervisor (measurements, region diffs, previews) to form one
falsifiable repair hypothesis at a time. Cite what you observed, what
you would change in the candidate source, and what geometric fact
would refute the hypothesis. The residual facts the supervisor returns
inform your reasoning; they never prescribe an edit.

## Bounded loop shape

The bounded loop the supervisor enforces is:

1. `workspace_status` to read the initial permitted intents and budgets.
2. Author an initial plan at `/candidate/plan.json` and pass it to
   `start_attempt`. Then write your Step 0 source under
   `/candidate/work/source/`.
3. Use `run_candidate_tool` to build, preview, and measure the
   candidate. Each call returns fresh handles.
4. Use `submit_step_zero` to submit the measured initial step. The
   supervisor retires that Attempt and resets `/candidate/work`.
5. Loop: form a repair hypothesis. Start the next Attempt with an
   explicit parent step handle; the supervisor reseeds
   `/candidate/work/source/` with the parent's source. Edit it,
   run the registered tools for the child, then `submit_repair`.
   Stop when residuals establish acceptance, when no further coherent
   repair is plausible, or when the supervisor reports
   `budget_exhausted`.
6. `select_and_finalize` with the strongest plausible step handle and
   authored notes.

You always read `permitted_next_intents` in the previous response and
issue only an intent it lists. Any other intent returns
`state_conflict`.

## Stop reasons and honesty

Never claim acceptance, provenance, or verification you did not
observe in an intent response. When you stop early, name one honest
stop reason drawn from what the responses tell you:

- `acceptance_reached` — the last submitted step's residuals meet the
  acceptance signal in the supervisor result.
- `no_feasible_repair` — you cannot form a coherent falsifiable
  hypothesis from current evidence.
- `budget_exhausted` — the surface returned this classification.
- `unsupported_domain` — the domain cannot be represented honestly as
  STEP-first parametric CAD.

Report exactly what handles the supervisor returned. Do not invent
identifiers.

## Progressive references

- `references/candidate-authoring.md` — patterns for writing STEP-first
  parametric candidate source under `/candidate/work/source/`.
