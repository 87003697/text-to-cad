# DXF Examples

Small 2D DXF fixtures for exercising the `dxf` skill tooling, all in one flat
folder: Python `gen_dxf()` generator sources alongside raw imported `.dxf`
files. Everything here is intentionally simple so failures point at the
tooling, not the fixture.

## Generated sources (`.dxf.py` / `.step.py`)

Build them with the DXF skill CLI (`python skills/dxf/scripts/dxf <source>`);
drawing packages land in the gitignored `__cadgen__/` cache and `.dxf` exports
are written on demand only, so no generated DXF output is committed. Together
they cover the skill's standalone-drafting and STEP-projection workflows.

- `gasket_plate.dxf.py` — standalone drafting: rounded-rectangle gasket
  outline (lwpolyline bulge arcs), four bolt holes, center cutout, and an
  engraved alignment crosshair on an `ENGRAVE` layer.
- `l_bracket_flat.dxf.py` — standalone sheet-metal flat pattern: rectangular
  blank, four mounting holes, and a dashed bend line on a `BEND` layer.
- `clamp_plate.step.py` + `clamp_plate.dxf.py` — STEP-projection workflow: the
  `.dxf.py` path-loads the sibling `.step.py` and projects its top-face
  topology to a cut profile with `cadgen.flatten` (outline, two bolt holes,
  center slot).

## Imported files (`.dxf`)

Raw DXF files from permissively licensed (MIT) test suites, committed via Git
LFS. They cover R12 (AC1009) and R2013+ (AC1027) flavors and a spread of entity
types.

**Every file here encloses at least one closed area.** That is the selection
rule, and it exists because the viewer renders a DXF by extruding its closed cut
contours into a 3D flat pattern — a drawing with no area has nothing to extrude
and nothing to show. Files that were only open paths (a lone arc, two open
splines, an INSERT grid of open flag symbols), an empty modelspace, or purely
degenerate geometry were removed for that reason; see the removal note below.

Several of these deliberately mix closed cut profiles with open annotation
(dimension extension lines, stray arcs), because real drawings do — layer intent
is what separates the two, not the entity type.

From [skymakerolof/dxf](https://github.com/skymakerolof/dxf) (`test/resources`,
MIT):

- `alu_extrusion_profile.dxf` — an aluminium extrusion cross-section: nine
  nested closed LWPOLYLINE chambers, two HATCH regions, and seven DIMENSION
  annotations across several layers and colors. The most realistic engineering
  part in the set. Upstream name: `alu-profile.dxf`.
- `plate_four_holes.dxf` — an OpenSCAD 2D export: a plate outline with four
  circular holes, written as 452 individual LINE segments that chain into closed
  loops with no dangling ends. Exercises the contour walk hard, since not one
  entity is closed on its own. Upstream name: `openscad_export.dxf`.
- `square_and_circle.dxf` — a square outline with an inscribed circle on
  separate colored layers; the circle is tangent to the square, a useful
  near-degenerate case for contour resolution. Upstream name:
  `squareandcircle.dxf`.
- `block_square_in_circle.dxf` — a circle plus an INSERT whose block holds a
  closed square, and a second standalone circle. Small, and the simplest file
  here that requires block expansion. Upstream name: `accumulatortest.dxf`.
- `circles_ellipses_arcs.dxf` — two closed ELLIPSE entities and a CIRCLE
  alongside two open ARCs. Closed-area ellipse coverage with open geometry
  mixed in. Upstream name: `circlesellipsesarcs.dxf`.

From [gdsestimating/dxf-parser](https://github.com/gdsestimating/dxf-parser)
(`test/data`, MIT):

- `laser_text_outlines.dxf` — the word "LaserWeb" as twelve legacy POLYLINE
  letter outlines, including the counters inside `a`, `e` and `b`. Closed by
  coincident first/last vertices rather than the closed flag, so it also covers
  that distinction. A genuine laser-cut profile. Upstream name: `polylines.dxf`.
- `overlapping_ellipses.dxf` — two full closed ELLIPSE entities that overlap.
  Minimal ellipse coverage. Upstream name: `ellipse.dxf`.

From [mozman/ezdxf](https://github.com/mozman/ezdxf) (`examples_dxf`, MIT):

- `nested_hole_shapes.dxf` — eight shapes with nested holes: rectangles inside
  rectangles, notched profiles, and pentagons, as sixteen closed LWPOLYLINE
  boundaries with ten HATCH fills. The best coverage of holes and nesting depth.
  Upstream name: `hatches_1.dxf`.

### Removed fixtures

These were dropped when the folder was reset around the closed-area rule. All
were MIT-licensed and are trivially recoverable from the upstream repositories
above if a validator-robustness suite ever wants them back:

- `arc1.dxf` — a single open ARC.
- `splines.dxf` — two open SPLINE entities.
- `multi_insert_with_attribs.dxf` — an INSERT grid of open flag symbols.
- `minimal_r12.dxf` — the 35-byte minimal R12 skeleton, no modelspace entities.
- `circle_radius_le_0.dxf` — two degenerate CIRCLE entities (radius `0.0` and
  `-1.0`), so no drawable area at all.

Note that the last two were also the folder's intentional-failure fixtures for
the drawing validator (`empty_drawing`, `zero_length_entity`). Nothing under
`tests/` referenced them, so no test coverage was lost, but the validator no
longer has a committed example of either condition.

Validate any of them post-hoc with:

```bash
python skills/dxf/scripts/dxf --validate models/dxf/<file>.dxf
```
