# Vision And Reconstruction Limits

Use this reference before extracting or modeling from any drawing image.

## Why This Exists

LLMs can describe a drawing convincingly while still placing features in the wrong
topology. Technical drawings punish that failure mode: a plausible bracket can be
entirely wrong. The skill must therefore convert image interpretation into
auditable evidence before any STEP geometry is authored.

## Common Bottlenecks

- Small details: decimal points, limit values, datum triangles, leader endpoints,
  modifiers, and hidden lines are easily missed or misread.
- Projection ambiguity: a single isometric or pictorial view is not a true
  top-view map. Screen-space positions cannot be used as CAD coordinates without
  a calibrated projection.
- Occlusion: hidden cutouts, hole depths, backside reliefs, and through/blind
  distinctions are often not visible.
- Topology hallucination: freehand unions of rectangles, circles, slots, and
  notches tend to create impossible or mismatched footprints.
- Pattern hallucination: apparent symmetry, repetition, or alignment may be
  presentation convenience rather than a manufacturing constraint.
- Weak visual validation: a generated CAD render can look mechanical while still
  failing the source drawing.

## Input Classification

Classify the source before modeling:

- `source_kind`: raster image, scanned PDF, vector PDF, CAD-derived drawing,
  STEP with PMI, or native CAD.
- `view_set`: single pictorial, orthographic set, section/detail views, auxiliary
  views, or unknown.
- `geometry_extractability`: vector contours available, raster contours
  measurable, annotation-only, or not measurable.
- `scale_basis`: explicit scale, dimension-derived scale, known bounding box, or
  none.
- `modeling_status`: model-ready, partial-model-ready, extraction-only, or
  blocked.

When `view_set` is single pictorial and no calibrated projection or source CAD is
available, precise footprint reconstruction is usually `blocked` or
`partial-model-ready`, not model-ready.

## Evidence Gates

Before a feature can be modeled, record:

- Feature ID and kind.
- Source view or crop ID.
- Source coordinates or crop bounds.
- Size evidence: explicit dimension, derived dimension, or approved assumption.
- Location evidence: explicit datum/baseline, derived construction relation, or
  approved assumption.
- Topology evidence: what the feature cuts, joins, or references.
- Confidence: high, medium, low, or unresolved.
- Validation method: measurement, topology query, rendered overlay, or manual
  review target.

Do not model a design-critical feature when size, location, or topology is
`unresolved`. Put it in the sidecar and ask for the missing source information.

## Required Stop Conditions

Stop and ask, or produce only an extraction/PMI sidecar, when any of these are
true:

- The user asks for a precise STEP from a single raster pictorial view and key
  locations are not dimensioned.
- The outer footprint is not explicitly dimensioned or vector-traceable.
- The model would require guessing hidden geometry, backside features, or cut
  depths.
- Two or more plausible interpretations produce different topology.
- A rendered comparison cannot be made and no numeric dimensions constrain the
  contested geometry.

Approximate concept models are allowed only when the user explicitly accepts an
approximate output. Mark them as approximate in file names, validation reports,
and final summaries.

## Better Recovery Paths

When the drawing is underconstrained, prefer one of these paths:

- Ask for the original STEP, native CAD, vector PDF, or orthographic drawing.
- Ask for a top-view sketch or a table of feature center coordinates.
- Extract only AP242-friendly PMI metadata and a feature ledger.
- Create a skeleton model containing only constrained datum planes, axes, holes,
  and dimensions, leaving unconstrained surfaces out or visibly simplified.
- Create a trace overlay or calibration worksheet before building geometry.

## Anti-Patterns

Avoid these behaviors:

- Building the base from a handful of visually placed rectangles and circles.
- Inferring symmetry because the object "looks symmetric" in perspective.
- Treating a rendered isometric screenshot as validation of geometry.
- Calling a model precise when most locations are proportional estimates.
- Hiding major assumptions only in prose; put them in the sidecar and validation
  report.
