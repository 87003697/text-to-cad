# F1 concept car — builder brief

Read this completely before writing geometry. Then read `f1_parts/spec.py`
(the contract) and `f1_parts/lib.py` (the vocabulary).

## The object

An original modern ground-effect Formula 1 car. Not a copy of any team's car.
No liveries, logos or sponsor marks anywhere. Carbon, exposed metal, and one
restrained vermillion accent — nothing else.

**Aesthetics are the primary objective.** This is not an engineering exercise.
Where beauty and function conflict, choose beauty. Regulations, manufacturability
and CFD-realism do not get a vote. The target is a launch render people stop
scrolling for.

## The rubric you are judged against (priority order)

1. Silhouette reads as a striking solid black shape at thumbnail size, and
   reads instantly as F1 from the side.
2. Aero surface language is consistent — the vocabulary in the front wing
   repeats in the floor fences, brake ducts and beam wing.
3. Flap cascades are rhythmic: gaps, chord lengths and twist progress smoothly
   element to element, never arbitrary.
4. Sidepod undercut is dramatic and the airflow story is legible at a glance.
5. Feature lines terminate deliberately — into another line, an edge, or a
   panel break. Never fading into nothing.
6. Suspension members read as delicate and highly stressed, not chunky:
   visible tapering, blade sections.
7. Trailing edges are genuinely thin. No slab-sided aero parts.
8. Zero faceting, zero missing fillets, zero unblended intersections at
   render resolution.

A blind critic will compare a render of the whole car — with your part in it —
against a current-generation F1 car. Your part passes only if the car wins.
A gorgeous endplate that fights the sidepod is a **fail**.

## Hard rules

- **Only edit your own module** in `f1_parts/`. Never edit `spec.py`,
  `lib.py`, `f1.step.py`, `f1.params.js`, or another specialist's module. If
  you need a shared datum changed, say so in your final report — do not change
  it. Other specialists are editing their modules at the same time.
- **Keep your module's public builder names and signatures exactly as they
  are.** The assembly calls them by name.
- **Use `lib` for everything structural.** Aero surfaces come from
  `lib.wing_element()` / `lib.airfoil_face()`. Members in the airstream come
  from `lib.blade_member()` / `lib.blade_path()`. Sculpted bodywork comes from
  `lib.body_loft()` over `lib.section_face()` / `lib.half_section_face()`.
  Extruded rectangles and bare cylinders are visible from a mile away — a
  swept-rectangle wing is an automatic fail.
- **Every exported body goes through `lib.styled(shape, label, color)`** with
  a colour from `spec`'s palette, and every builder returns
  `lib.group("<name>", [...])`.
- **Never call `shape.bounding_box()`** — it tessellates, mutates the shared
  TShape and breaks cadgen's content-addressed dedup. Use `lib.bbox(shape)`.
- **Delete your module's `_massing` import** once real geometry lands.
- **Do not run `scripts/gen` on `f1.step.py`.** That package is rebuilt
  centrally; concurrent builds fight over its cache. Use your part entry.
- All dimensions come from `spec`. If a number matters to another part, it is
  already in `spec` — look for it rather than inventing a duplicate.

## Coordinates (repeated because it is the most common mistake)

    +X forward (toward the nose)   +Y left   +Z up
    origin = ground plane, centreline, FRONT AXLE

Front axle x=0, rear axle x=-3600, ground z=0, nose tip x=+1420,
rear wing trailing edge x=-4180, roll hoop crown z=950.

Model the left (+Y) side and use `lib.pair()` for the mirrored twin, or build
symmetric bodies about y=0 directly.

## Toolchain

Use this interpreter (the worktree has no venv of its own):

```
PY=/Users/jakefitzgerald/robots/text-to-cad/.venv/bin/python
```

Run everything from the repo root
`/Users/jakefitzgerald/robots/text-to-cad/.claude/worktrees/f1-car-cad-model-9289dd`.

Build your part (fast; the whole car is not rebuilt):

```
$PY skills/cad/scripts/gen models/one-shots/f1/parts/<YOUR_ENTRY>.step.py
```

Validate it:

```
$PY skills/cad/scripts/inspect refs models/one-shots/f1/parts/<YOUR_ENTRY>.step.py --facts --planes --positioning
```

Look at it on the presentation stage (this is how the critic will see it —
never review your work on the default workbench theme, it lies about form):

```python
import sys; sys.path.insert(0, "tmp")
import render
job = {
  "input": "models/one-shots/f1/parts/<YOUR_ENTRY>.step.py",
  "mode": "view",
  "appearance": render.theme(),
  "display": {"mode": "solid"},
  "render": {"sizeProfile": "simple", "padding": 0.03, "viewLabels": True},
  "outputs": [
    {"path": "tmp/f1render/<you>_fq.png",
     "camera": {"direction": [1, 0.62, 0.20], "zoom": 2.0}, "viewLabel": "fq"},
    {"path": "tmp/f1render/<you>_side.png",
     "camera": {"direction": [0, 1, 0.06], "zoom": 2.0}, "viewLabel": "side"},
  ],
}
render.run(job, "job_<you>")
```

Camera `direction` is the vector from the target toward the camera, in car
coordinates, and auto-fits; `zoom` ~2.0 fills the frame. Useful directions:

| view                  | direction            |
|-----------------------|----------------------|
| front three-quarter   | `[1, 0.62, 0.20]`    |
| low front three-quarter | `[1, 0.55, 0.10]`  |
| side                  | `[0, 1, 0.06]`       |
| top                   | `[0, 0.001, 1]`      |
| rear three-quarter    | `[-1, 0.55, 0.28]`   |
| head-on low           | `[1, 0.05, 0.10]`    |

Then **read the PNG back with the Read tool and actually look at it.** Iterate
on what you see. Deterministic checks passing is not evidence that a surface
is beautiful.

## Validation you must pass before reporting done

1. `scripts/gen` on your part entry exits 0.
2. `scripts/inspect refs ... --facts` reports your solids valid, watertight,
   with no self-intersections.
3. Your bodies are inside the package envelope and do not interpenetrate
   neighbouring parts (check the shared hardpoints and stations in `spec`).
4. You have looked at at least a front three-quarter and a side render of your
   own part and are satisfied it meets the rubric.

## Report back

- What you built, body by body.
- Which `spec` datums you consumed, and any you wish existed.
- Any interpenetration or hand-off risk with a neighbouring part.
- The validation output you actually ran.
- Your render paths.
