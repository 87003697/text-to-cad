# Drawing Extraction Schema

Use this reference when turning a drawing image, PDF, or scan into a structured CAD specification.

## Goal

Create a semantic sidecar that is friendly to STEP AP242 concepts without requiring the agent to author raw EXPRESS/STEP entities. Store the drawing source evidence, semantic PMI, and target geometry bindings separately so a later exporter can map them to AP242 PMI.

## Detail Discipline

Assume first-pass image understanding will miss small drawing details. Before finalizing the spec:

- Inspect the full sheet for context, then inspect high-resolution crops for every dense annotation area.
- Tile large drawings so small text, leader endpoints, datum triangles, decimal points, plus/minus signs, diameter symbols, and modifier letters are examined at readable scale.
- Make a pass over the drawing border and title block; revisions, units, drawing IDs, and general notes are often small.
- Trace each leader from annotation to target feature. If the endpoint is hidden by geometry, record the ambiguity.
- Keep source coordinates and confidence for every extracted callout.
- Prefer "unresolved" over "not present" unless the relevant crop was checked.

## Top-Level Shape

```json
{
  "schema": "drawing-to-cad.pmi.v1",
  "source": {
    "file": "<drawing-source>",
    "sheet_title": "",
    "drawing_id": "",
    "revision": "",
    "units": "mm",
    "standard_hint": "",
    "projection": "unknown",
    "image_size_px": [0, 0]
  },
  "product": {
    "name": "",
    "part_number": "",
    "description": ""
  },
  "coordinate_system": {
    "origin": "inferred",
    "primary_view": "",
    "x_axis": "",
    "y_axis": "",
    "z_axis": ""
  },
  "extraction_quality": {
    "source_kind": "raster image",
    "view_set": "unknown",
    "geometry_extractability": "unknown",
    "scale_basis": "unknown",
    "modeling_status": "unknown",
    "blocking_reasons": []
  },
  "evidence_ledger": [],
  "datums": [],
  "features": [],
  "pmi": [],
  "presentation": [],
  "assumptions": [],
  "open_questions": []
}
```

`modeling_status` must be one of:

- `model-ready`: enough dimensions, views, or vector geometry exist to build the
  requested model.
- `partial-model-ready`: some features can be modeled, but design-critical
  geometry remains unresolved.
- `extraction-only`: PMI, features, datums, and notes can be extracted, but the
  requested STEP would be mostly guesswork.
- `blocked`: the task needs another source or a user decision before useful
  geometry can be generated.

For a single raster pictorial view, default to `partial-model-ready`,
`extraction-only`, or `blocked` unless explicit dimensions or a calibrated
projection constrain the 3D geometry.

## Evidence Ledger

Use the evidence ledger as the bridge between visual interpretation and CAD
geometry. Every modeled feature should have at least one evidence entry.

```json
{
  "id": "evidence:right_lobe_hole",
  "target_feature": "right_lobe_hole",
  "source_view": "isometric",
  "source_crop": "presentation:right_lobe_crop",
  "source_coordinates_px": [],
  "size_evidence": "diameter 34.8 - 35.2 callout",
  "location_evidence": "unresolved",
  "topology_evidence": "visible through hole in rounded lobe",
  "confidence": "medium",
  "model_ready": false,
  "validation_method": "diameter measurement only; location unresolved"
}
```

Set `model_ready` to `false` when size, location, or topology is unresolved.
Do not model design-critical geometry from `false` entries unless the user has
explicitly accepted an approximate concept model.

## Datums

Represent datum features as geometry targets, not only boxed letters.

```json
{
  "id": "datum_A",
  "label": "A",
  "kind": "datum_feature",
  "target_feature": "top_primary_face",
  "target_geometry": ["face:top_primary_face"],
  "source_evidence": ["presentation:datum_A_tag"]
}
```

Use one datum object per datum label. If the drawing only shows a tag and the target face is ambiguous, keep `target_feature` empty and add an open question or assumption.

## Features

Give every relevant shape aspect a stable ID. Bind future dimensions and tolerances to these IDs.

```json
{
  "id": "right_lobe_hole",
  "kind": "cylindrical_hole",
  "role": "manufacturing_feature",
  "target_geometry": ["axis:right_lobe_hole", "surface:right_lobe_hole_wall"],
  "pattern_id": "mounting_hole_pattern",
  "source_evidence": ["presentation:diameter_right_lobe_hole"]
}
```

Common `kind` values:

- `planar_face`
- `cylindrical_hole`
- `counterbored_hole`
- `slot`
- `obround_slot`
- `extruded_boss`
- `pocket`
- `outside_profile`
- `edge_round`
- `chamfer`
- `axis`
- `hole_pattern`

## Dimensions

Store size and location dimensions as typed semantic objects.

```json
{
  "id": "size_right_lobe_hole",
  "type": "dimensional_size",
  "characteristic": "diameter",
  "target_feature": "right_lobe_hole",
  "nominal": 35.0,
  "limits": {
    "lower": 34.8,
    "upper": 35.2
  },
  "unit": "mm",
  "source_text": "diameter 34.8 - 35.2"
}
```

For a bilateral or unilateral tolerance:

```json
{
  "id": "size_side_hole",
  "type": "dimensional_size",
  "characteristic": "diameter",
  "target_feature": "side_hole",
  "nominal": 20.0,
  "tolerance": {
    "upper": 0.05,
    "lower": -0.10
  },
  "unit": "mm"
}
```

For a location or angle:

```json
{
  "id": "angle_notch_face",
  "type": "dimensional_location",
  "characteristic": "angle",
  "target_feature": "angled_notch_face",
  "nominal": 60.0,
  "tolerance": {
    "plus_minus": 0.5
  },
  "unit": "deg"
}
```

## GD&T And PMI

Store feature control frames as geometric tolerances. Preserve symbols semantically.

```json
{
  "id": "position_mounting_holes",
  "type": "geometric_tolerance",
  "characteristic": "position",
  "target_feature": "mounting_hole_pattern",
  "tolerance_zone": {
    "kind": "diameter",
    "value": 0.75,
    "unit": "mm"
  },
  "datum_reference_frame": ["A", "B", "C"],
  "modifiers": [],
  "source_text": "position diameter 0.75 | A | B | C"
}
```

Common `characteristic` values:

- `flatness`
- `straightness`
- `circularity`
- `cylindricity`
- `profile_of_line`
- `profile_of_surface`
- `parallelism`
- `perpendicularity`
- `angularity`
- `position`
- `concentricity`
- `symmetry`
- `circular_runout`
- `total_runout`

## Presentation Evidence

Keep visual evidence so extraction can be audited.

```json
{
  "id": "presentation:position_mounting_holes",
  "kind": "feature_control_frame",
  "view": "isometric",
  "source_text": "position diameter 0.75 | A | B | C",
  "bbox_px": [0, 0, 0, 0],
  "leader_points_px": [],
  "confidence": 0.0
}
```

Presentation evidence should never be the only representation of a dimension or tolerance. Create a semantic `pmi` object and link it back to presentation evidence.

## Ambiguity Policy

When a drawing cannot uniquely define a model:

- Add an `open_questions` entry if the missing fact blocks the STEP.
- Add an `assumptions` entry if a conservative inference is acceptable.
- Keep the original source text and screen location for every uncertain extraction.
- Never silently invent design-critical geometry.
- If uncertainty changes topology or visible footprint, set
  `extraction_quality.modeling_status` to `partial-model-ready`,
  `extraction-only`, or `blocked`.
- Record the uncertain feature in `evidence_ledger` with `model_ready: false`
  instead of encoding it as precise geometry.
