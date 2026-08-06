# DXF Examples

Small 2D DXF fixtures for exercising the `dxf` skill tooling: Python
`gen_dxf()` generators on one side, raw imported `.dxf` files on the other.
Everything here is intentionally simple so failures point at the tooling, not
the fixture.

## generated/

Python `.dxf.py` sources covering the skill's generator workflows. Build them
with the DXF skill CLI (`python skills/dxf/scripts/dxf <source>`); drawing
packages land in the gitignored `__cadgen__/` cache and exports are written on
demand only, so no generated `.dxf` output is committed.

- `gasket_plate.dxf.py` — standalone drafting: rounded-rectangle gasket
  outline (lwpolyline bulge arcs), four bolt holes, center cutout, and an
  engraved alignment crosshair on an `ENGRAVE` layer.
- `l_bracket_flat.dxf.py` — standalone sheet-metal flat pattern: rectangular
  blank, four mounting holes, and a dashed bend line on a `BEND` layer.
- `clamp_plate.step.py` + `clamp_plate.dxf.py` — STEP-projection workflow: the
  `.dxf.py` path-loads the `.step.py` and projects its top-face topology to a
  cut profile with `cadgen.flatten` (outline, two bolt holes, center slot).

## imported/

Raw `.dxf` files downloaded from permissively licensed (MIT) test suites,
committed via Git LFS. They cover both R12 (AC1009) and R2013+ (AC1027)
flavors and a spread of entity types, including files that intentionally fail
the skill's drawing checks — useful fixtures for validator and viewer
robustness.

From [gdsestimating/dxf-parser](https://github.com/gdsestimating/dxf-parser)
(`test/data`, MIT):

- `arc1.dxf` — single ARC on a non-cut-named layer (R12). Validation: FAIL
  (`open_cut_profile`), as expected for an open arc.
- `ellipse.dxf` — two ELLIPSE entities (R12). Validation: ok.
- `splines.dxf` — two SPLINE entities (R12). Validation: FAIL
  (`units_not_set`, `open_cut_profile`), as expected for open splines.
- `polylines.dxf` — twelve legacy POLYLINE entities (R12). Validation: ok.

From [mozman/ezdxf](https://github.com/mozman/ezdxf) (`examples_dxf`, MIT):

- `minimal_r12.dxf` — the 35-byte minimal R12 skeleton
  (`Minimal_DXF_AC1009.dxf`). Validation: FAIL (`empty_drawing`), as expected
  for an empty modelspace.
- `multi_insert_with_attribs.dxf` — block INSERT with attributes (R2013).
  Validation: ok.
- `circle_radius_le_0.dxf` — two zero-radius CIRCLE entities (R2013).
  Validation: FAIL (`zero_length_entity` x2), as expected for degenerate
  geometry.

Validate any of them post-hoc with:

```bash
python skills/dxf/scripts/dxf --validate models/dxf/imported/<file>.dxf
```
