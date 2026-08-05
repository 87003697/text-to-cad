# Hypercar part-builder brief

Read this in full before writing any geometry. It is the contract every part
module obeys so the car reads as ONE design instead of a parts bin.

## Repo / commands

Repo root (a git worktree — use this path, not the main checkout):

```
/Users/jakefitzgerald/robots/text-to-cad/.claude/worktrees/mid-engine-hypercar-cad-a2e3bc
```

Python: `/Users/jakefitzgerald/robots/text-to-cad/.venv/bin/python`

Run everything **from the repo root**:

```bash
# build your part's isolated review entry
/Users/jakefitzgerald/robots/text-to-cad/.venv/bin/python skills/cad/scripts/gen \
  models/one-shots/hypercar/.review/<mod>/<mod>.step.py

# render it (helper writes the JSON job for you)
/Users/jakefitzgerald/robots/text-to-cad/.venv/bin/python \
  /private/tmp/claude-501/-Users-jakefitzgerald-robots-text-to-cad--claude-worktrees-mid-engine-hypercar-cad-a2e3bc/2330a79e-a7cc-4d4e-9534-d6eaed3a76a8/scratchpad/shot.py \
  models/one-shots/hypercar/.review/<mod>/<mod>.step.py <stem> \
  --views fq,side,rq,top --size assembly --theme workbench
```

Then `Read` the printed PNG paths to look at your work. **Always look at your
renders.** Use `--theme presentation` for beauty checks, `--theme workbench`
for geometry checks.

**Do NOT set `CADGEN_WARM=1`.** The warm daemon hangs in this worktree.

## Frame and package (from `hypercar_parts/surfaces.py` — import, never retype)

```
+X forward (nose)   +Y car left   +Z up   ground at Z=0   origin at wheelbase centre
```

| | |
|---|---|
| length / width / height | 4700 × 2050 × 1150 |
| wheelbase | 2700 (`S.FRONT_AXLE_X` = +1350, `S.REAR_AXLE_X` = −1350) |
| nose / tail | `S.NOSE_X` = +2300, `S.TAIL_X` = −2400 |
| front wheel | 21″ rim, 285/35R21 → OD 732.9, hub at (+1350, ±850, 366.5) |
| rear wheel | 22″ rim, 355/30R22 → OD 771.8, hub at (−1350, ±830, 385.9) |
| arch radii | `S.FRONT_ARCH_R` 392.5, `S.REAR_ARCH_R` 413.9 |
| lowest body point | Z ≈ 77 |

`S.wheel_centres()` returns `(x, y, z, tyre_r, tyre_w, rim_d)` per corner.
Every body dimension you might need is a callable control curve of X:
`S.DECK_Z(x)`, `S.CREST_Z(x)`, `S.HALF_WIDTH(x)`, `S.MAXW_Z(x)`, `S.SILL_Y(x)`,
`S.SILL_Z(x)`, `S.FLOOR_Z(x)`, `S.FLOOR_Y(x)`, plus canopy equivalents
`S.CANOPY_TOP_Z/HW/MAXW_Z/BASE_Z(x)` over `S.CANOPY_X0`(780) … `S.CANOPY_X1`(−1240).

**Query the surface, never guess a coordinate.** If your part touches the body,
place it against `S.HALF_WIDTH(x)` / `S.CREST_Z(x)` etc. so it stays attached
when the surface is tuned.

## Module contract

Write exactly one file: `models/one-shots/hypercar/hypercar_parts/<mod>.py`

```python
from build123d import ...
from hypercar_parts import surfaces as S
from hypercar_parts.context import group, style
from hypercar_parts import palette as P


def build():
    """Return ONE labelled group Compound."""
    kids = [...]
    return group("<mod>", kids)
```

Rules that are not negotiable — each one is a real failure mode here:

1. **Leaves carry colour, groups do not.** A colour set on a group compound is
   silently ignored by the render package. Use `style(shape, "label", P.BODY)`
   on every leaf.
2. **Colours are authored as sRGB hex** via `palette`. Never write raw float
   triples — the renderer treats channels as *linear* RGB, so `0.5` shows as
   `#BCBCBC`.
3. **`Compound(children=...)` reparents.** The same shape object cannot appear
   in two compounds. Build a fresh shape per occurrence, or use
   `cadgen.compound_from_instances(name, [(prototype, Location, name), ...])`
   for anything repeated ≥4× (spokes, bolts, fins) — `part.moved()` in a loop
   deep-copies the whole shape graph and is very slow.
4. **`Compound(obj=[...])` without `children=` collapses to ONE occurrence.**
   Always pass `children=`.
5. **No 3D fillets after booleans.** OCC segfaults uncatchably on this geometry
   class. Build roundness into 2D profiles before extruding/lofting, or use
   `RectangleRounded` / sketch-vertex fillets. If you must try a 3D fillet,
   wrap it and fall back to the unfilleted solid.
6. **Overshoot boolean cutters** ~1 mm past both faces. Coplanar tool/target
   faces are a classic kernel failure.
7. Label leaves `role:placement`, e.g. `upper_wishbone:front_left`. No spaces,
   no `:` inside a token.

## Aesthetic rubric (priority order — this is what you are judged on)

1. Silhouette reads as a striking solid black shape at thumbnail size.
2. Highlight/reflection lines run continuously across panel breaks — no
   unintended kinks.
3. Feature lines terminate deliberately: into another line, an edge, or a
   shutline. Never fade into nothing.
4. Wheel-to-arch relationship: gap, tuck and section width look planted.
5. G2 where the eye expects smooth; creases deliberate and razor sharp.
6. Greenhouse graphic: DLO shape, pillar thickness, tumblehome.
7. Surface tension — no slack, no bloat, no dead flat areas.
8. Zero faceting, zero missing fillets, zero unblended intersections.

**Wherever beauty and function conflict, choose beauty.** This is not an
engineering exercise. Do not let packaging or manufacturability dilute it.

## Design language

Deep liquid-graphite painted body, exposed carbon aero (splitter, rocker,
diffuser, wing), bronze jewellery as the single warm accent, smoked glass,
satin-aluminium mechanicals. Cool body + one warm accent.

The car's identity is a **single-gesture teardrop**: a taut monolithic upper
volume riding on a heavily undercut lower body, with a hard shoulder crease
running the full length, front fender crests standing proud of a deep hood
valley, and rear flying buttresses standing proud of a sunken engine deck.

Detail should look **machined and jewel-like**, not chunky. Think concept-car
reveal stand, not parts catalogue.

## Validation before you report done

- `scripts/gen` on your review entry exits 0.
- You have LOOKED at renders from at least 4 views and iterated.
- Nothing of yours interpenetrates the body surface or the wheels unless it is
  meant to (query `S.*` to check).
- Report: the module path, what you built, the render paths, and anything you
  could not resolve.
