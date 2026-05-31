# Validation

Use this reference after generating a STEP file.

## Required Checks

Run the local STEP launcher and inspection command from this skill directory:

```bash
python scripts/step "$MODEL_SOURCE"
python scripts/inspect refs "$STEP_OUTPUT" --facts --planes --positioning
```

The validation report must state exactly what ran.

Validation cannot rescue an underconstrained extraction. If a feature was created
from an unresolved evidence-ledger entry, the validation status for a precise
model must fail or be marked approximate.

## Geometry Checklist

Compare the generated STEP against the extracted drawing spec:

- Overall bounding box and thickness.
- Datum faces exist and have the expected orientation.
- Hole diameters, axes, and through/blind direction.
- Slot widths, end radii, and center-to-center lengths.
- Boss heights, side counts, and placement.
- Pocket/cutout depths and wall locations.
- Angles and chamfers.
- Pattern counts and spacing.
- Symmetry declarations, including a numeric check that transformed feature
  centers match the declared symmetry.
- Evidence coverage: every modeled feature appears in the evidence ledger and
  has `model_ready: true` or is marked as an approved approximation.

Write focused Python measurement snippets when the standard inspection output does not prove a required value. Keep temporary snippets in a disposable scratch location, not in this skill's `scripts/`.

## PMI Checklist

Ensure `model.pmi.json` preserves:

- Every dimension visible in the drawing.
- Every datum label and target feature.
- Every GD&T frame, tolerance value, modifier, and datum reference frame.
- Every note that affects manufacturing or interpretation.
- Every uncertainty and assumption.

PMI metadata does not by itself prove geometry. Geometry checks prove the nominal shape; the sidecar preserves tolerancing intent.

## Visual Review

When a CAD viewer or rendering workflow is available, review the generated STEP
from the same approximate view as the source drawing. Visual review is useful
for missing features and orientation errors but does not replace numeric checks.

For image-driven reconstruction, prefer an overlay-style check:

- Render the model from the declared source view or calibrated projection.
- Compare the rendered silhouette, major holes, slots, and bosses against the
  source image or traced features.
- Record whether the comparison is quantitative, manual, or unavailable.
- If the overlay is unavailable and the source lacks full dimensions, do not
  mark the shape as a precise match.

An attractive isometric render is not enough evidence. It must be tied back to
the source drawing through dimensions, topology checks, or overlay comparison.

## CAD Viewer Handoff

For generated or modified `.step`, `.stp`, `.stl`, `.3mf`, `.dxf`, or native
`.glb` artifacts, the validation report should record the `$cad-viewer` handoff
status:

- `returned`: include the viewer link(s) returned for the explicit artifact
  path(s).
- `unavailable`: record that `$cad-viewer` was not installed or could not be
  started.
- `not_applicable`: use only when no supported CAD artifact was generated, such
  as an extraction-only or blocked reconstruction.

The viewer handoff complements, but does not replace, the required geometry,
PMI, evidence coverage, and overlay/view-comparison checks.

## Failure Handling

If validation fails:

1. Identify whether the error is extraction, modeling, or measurement.
2. Update `model.pmi.json` for extraction/metadata errors.
3. Update `model.py` for geometry errors.
4. Regenerate STEP.
5. Rerun only the failed checks plus any checks affected by the change.
