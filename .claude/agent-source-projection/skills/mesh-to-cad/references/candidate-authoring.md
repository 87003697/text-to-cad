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

Polygon coordinates are planar: the default polygon plane is XY. For a
semantic profile in the YZ plane, pass `Plane.YZ` explicitly (or map the
profile coordinates explicitly into canonical XYZ) rather than relying on a
default workplane. After every rotation or other transformed placement,
check the resulting component's final canonical XYZ bounds.

If a residual target lies outside the Reference bounds, or a transformed
component has an unexpected global bound, fix the source coordinates first;
do not widen the Reconstruction Spec to explain a placement error. Keep the
strict positive-volume overlap requirement for Component targets unchanged.

## STEP-first shape return

Compose primitives with build123d until `gen_step()` returns one
STEP-exportable solid or assembly. The trusted registered tool
performs the STEP export, the GLB measurement export, and the recipe
manifest; you never call `export_step`, write files under `work/`
outside `source/`, or touch `candidate.glb`. Doing so causes
`run_candidate_tool` to fail closed.

Use these build123d transform idioms:

- **Polygon plates:** extrude the polygon's existing face directly. In the
  installed runtime, wrapping the polygon in another `Face` creates an empty
  shape:

  ```python
  from build123d import Polygon, extrude

  wing = extrude(Polygon(*points, align=None), amount=thickness)
  ```

  If another operation specifically requires a face, use
  `Polygon(*points, align=None).faces()[0]`.
- **Independent solids:** preserve named body, wing, and tail solids with
  `Compound([body, wing, tail])`. Use a boolean only when the intended result
  must be one watertight fused solid; `+` performs boolean composition rather
  than collecting an assembly.

- **BuildPart 3D placement:** use plural `with Locations((x, y, z)):`. A
  `Location` or `Plane` value is not a context manager. If a workplane is
  needed, pass it to a supported builder constructor such as
  `BuildPart(Plane.XZ.offset(...))`; never write `with Location(...):` or
  `with Plane...`.
- **Uniform scaling:** `shape.scale(2.0)` returns a transformed value.
- **Nonuniform scaling in an active builder:** top-level `scale(...)` defaults
  to `Mode.REPLACE` there. Keep both the primitive and `scale` private, then
  explicitly add the transformed result so existing builder geometry remains:

  ```python
  from build123d import BuildPart, Location, Mode, Sphere, add, scale

  def gen_step():
      sx, sy, sz = 0.30, 0.18, 0.12
      x, y, z = 0.0, 0.16, 0.02
      with BuildPart() as part:
          sphere = Sphere(1.0, mode=Mode.PRIVATE)
          body = scale(sphere, by=(sx, sy, sz), mode=Mode.PRIVATE).moved(
              Location((x, y, z))
          )
          add(body)
      return part.part
  ```
- **Direct translation:** use `shape.moved(Location((x, y, z)))` or
  `Location((x, y, z)) * shape`.

All transforms return/use explicit values; do not discard them or expect
already-added builder geometry to mutate. If a candidate operation fails,
change the candidate source first, then reuse the same active Attempt's opaque
operation handle while its command budget permits; never repeat unchanged
source.

If changed source later measures
`change_from_parent.no_observable_geometry_change=true`, inspect the returned
shape construction first: empty shapes, boolean composition that discarded
components, and discarded transform results are the primary diagnosis. Change
chord, placement, or curvature only after the construction returns the intended
solids.

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
