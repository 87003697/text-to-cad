# Candidate authoring reference

Guidance for writing parametric candidate source under `/candidate/work/source/`.
The supervisor runs the registered build and measurement tools on this
source through the `run_candidate_tool` intent; you never invoke them
yourself.

## Entry module

Put your entry module at `/candidate/work/source/model.py`. It must
define one top-level, no-argument function named `gen_step()` that
returns a build123d shape, `Compound`, `Part`, or `Assembly` and can
be rebuilt from the same source with no external state. The trusted
tool discovers `gen_step()` by name; other function names, decorators,
or arguments cause the invocation to fail closed.

```python
from build123d import BuildPart, Box, Cylinder, Mode
from pathlib import Path


def _width() -> float:
    return float(Path("source/width.txt").read_text().strip())


def gen_step():
    with BuildPart() as part:
        Box(_width(), _width(), _width())
        Cylinder(radius=_width() / 4, height=_width(), mode=Mode.SUBTRACT)
    return part.part
```

## Sidecar parameter files

Read every configurable dimension from a sidecar file next to the
module, opened through a bundle-relative path:

```python
from pathlib import Path

width = float(Path("source/width.txt").read_text().strip())
```

- Never resolve `..` above `source/`.
- Never resolve absolute host paths; there are none you can use.
- Keep sidecars small, ASCII, and one-value-per-line unless a bounded
  JSON document is genuinely simpler.

The same sidecars are passed by the supervisor as `input` handles to
the registered build; the recipe reads them by the same bundle-relative
path.

## Coordinate contract

Author directly in the coordinate frame the initial summary observation
reports. Do not translate, rotate, or scale the candidate to align with
the reference. Alignment is not part of authoring; the Canonical
Reference is fixed and normalization is one supervisor-owned step
outside your sandbox.

## STEP-first shape return

Compose primitives with build123d until `gen_step()` returns one
STEP-exportable solid or assembly. The trusted registered tool
performs the STEP export, the GLB measurement export, and the recipe
manifest; you never call `export_step`, write files under `work/`
outside `source/`, or touch `candidate.glb`. Doing so causes
`run_candidate_tool` to fail closed.

`Location` is a transform value, not a context manager. Inside a
`BuildPart` context, place geometry with plural
`with Locations((x, y, z)):`. In direct shape flow outside a builder, return or
explicitly use `Location((x, y, z)) * shape` or
`shape.moved(Location((x, y, z)))`; do not discard the transformed value.
Never write singular `with Location(...):`. If a candidate operation fails,
change the candidate source first, then reuse the same active Attempt's opaque
operation handle while its command budget permits; never repeat unchanged
source.

## Repair edits

When you form a repair hypothesis, edit `/candidate/work/source/model.py`
(and sidecars) in place before starting the child Attempt. Keep edits
focused on the geometry the hypothesis names; unrelated churn dilutes
the residual signal.

- Prefer parametric edits over hard-coded numbers.
- If a repair requires more than one primitive, keep the edits within
  one coherent hypothesis; ask for a fresh Attempt for a different
  hypothesis.
- Rewrite comments and identifiers to match the new geometry; stale
  names mislead future repair reasoning.

## What not to author

- No absolute host paths.
- No filesystem access outside `/candidate/work` other than the fixed
  supervisor-owned control files (`plan.json`, `selection.json`,
  `notes.md`) named at `/candidate/*.json`.
- No network access; the sandbox network is closed to your process.
- No calls to registered candidate tools (build, preview, measure,
  diff) — these run only under `run_candidate_tool`.
- No calls to publication, storage, review, or finalization; those are
  supervisor-only intents.

## Error hygiene

If the candidate cannot be authored honestly as STEP-first parametric
CAD (for example the reference summary implies an inherently freeform
surface), stop and report `unsupported_domain` in your final
selection. Do not fabricate an approximation and claim acceptance.

If a build or measurement invocation returns a tool failure through
`run_candidate_tool`, read the classification the supervisor returned
and either author a targeted repair or stop with `no_feasible_repair`.
Never re-request the same operation without changing `/candidate/work/source/`.
