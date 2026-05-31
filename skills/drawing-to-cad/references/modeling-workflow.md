# Modeling Workflow

Use this reference after extracting a semantic drawing spec.

Do not start a CAD model until the extraction has a modeling status and evidence
ledger. The model should encode only geometry that is model-ready, plus
explicitly approved approximate placeholders.

## Source Layout

Prefer this output set in the user's project artifact directory:

```text
input drawing copy
model.py
model.pmi.json
model.validation.json
model.step
```

Use the drawing source stem if the user has not named the part.
When a CAD artifact is generated, include the `$cad-viewer` handoff result in
the validation report rather than encoding viewer-specific paths in the model
source.

## Build Order

1. Check `extraction_quality.modeling_status`. If it is `blocked` or
   `extraction-only`, do not create a precise STEP; create the sidecar/report
   and ask for the missing source.
2. Define units and nominal parameters from explicit dimensions or approved
   assumptions.
3. Establish datum construction planes and model origin.
4. Create the base body from vector contours, orthographic/profile dimensions,
   or a user-approved coordinate table. Do not freehand the base from a single
   pictorial raster.
5. Add bosses, pads, ribs, and raised features only when their location and
   join topology are constrained.
6. Cut holes, slots, pockets, and notches from evidence-ledger entries whose
   size, location, and topology are model-ready.
7. Add rounds/chamfers that are explicitly dimensioned or included in an
   approved approximate output.
8. Label or comment feature blocks with IDs that match `model.pmi.json`.
9. Return the shape, compound, or assembly from `gen_step()`.

## Python Pattern

```python
from build123d import *


UNITS = "mm"


class P:
    thickness = 12.0
    main_width = 120.0
    main_depth = 60.0
    hole_diameter = 10.0


def gen_step():
    with BuildPart() as part:
        with BuildSketch(Plane.XY):
            Rectangle(P.main_width, P.main_depth)
        extrude(amount=P.thickness)

        # feature: center_mounting_hole
        with Locations((0, 0, P.thickness)):
            Hole(radius=P.hole_diameter / 2)

    return part.part
```

Do not hardcode output paths in `gen_step()`. The launcher owns output paths.

## Nominal Values

For limit dimensions, choose a nominal value deliberately and record the choice in the sidecar:

- Use the stated nominal when present.
- For upper/lower limits without a nominal, use the midpoint unless the drawing standard or user says otherwise.
- For unilateral tolerances, use the stated dimension as nominal.

Do not use nominal-value rules to invent missing location dimensions. A hole
diameter callout makes the hole size model-ready; it does not make the hole
center model-ready.

## Feature IDs

Keep IDs stable and readable:

- `datum_A_top_face`
- `datum_B_left_face`
- `right_lobe_hole`
- `right_lobe_hole_axis`
- `slot_left_obround`
- `hex_boss_top_face`

Use the same IDs in:

- Python comments or labels.
- `model.pmi.json` features.
- Validation report references.
- Evidence ledger entries.

## Source-To-Model Discipline

For each modeled feature, keep a parameter or construction relation that traces
back to the evidence ledger. If a feature is placed by screen proportion, name
that parameter as an approximation and require user approval before calling the
result precise.

Preferred geometry sources, in order:

1. Native CAD or STEP with PMI.
2. Vector PDF/DXF contours with dimensions.
3. Orthographic views with enough dimensions to solve the profile.
4. Raster image with calibrated scale and traceable contours.
5. User-approved approximate concept geometry.

Symmetry, patterns, and equal spacing must be backed by a drawing callout,
datum/centerline evidence, vector geometry, or explicit user instruction. Do not
infer them only from an isometric appearance.

## Assemblies

For a drawing that describes an assembly, model components as separate generated parts only when the drawing has part boundaries or assembly callouts. Otherwise generate one STEP solid/compound and mark the uncertainty.

## Metadata-Only Items

Keep these out of the nominal solid unless the drawing requires geometry:

- GD&T tolerance zones.
- Datum symbols.
- Surface finish callouts.
- General notes.
- Material and finish.
- Thread designation if thread geometry is not required.
