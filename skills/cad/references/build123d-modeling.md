# build123d modeling patterns

Read this file when writing or repairing build123d Python source.

## Modeling objective

Create a valid STEP-ready BREP model, not a visual mesh. Prefer closed solids, explicit labels, and stable parametric dimensions. Define `gen_step()` returning the STEP-ready shape or labeled compound; the CLI owns output paths (see `step-generation.md`). Name a buildable entry generator `<name>.step.py` (the marker the viewer and build tools scan for); keep `<name>.py` for helper/library modules that are only imported, not built on their own (see "Entry generators are named `<name>.step.py`" in `step-generation.md`).

## Design strategy

Decide how the part is constructed before writing geometry code:

- **Choose the construction that makes the spec's dimensions direct parameters.** Profile-driven shapes get one closed sketch plus `extrude`/`revolve`/`sweep`/`loft`; block-and-feature parts get a base solid plus subtractive features. Prefer whichever construction lets the user's controlling dimensions appear as named parameters instead of derived values.
- **Decide part vs assembly before modeling.** Bodies that are separately manufactured, purchased, or movable belong in a labeled assembly (see `positioning.md`); monolithic manufacturing intent gets a single fused solid. Avoid unlabeled compounds of solids — multi-body output without occurrence labels loses traceability in inspection and viewer review.
- **Pick the origin and orientation from the functional datum before sculpting.** Model on the mating interface, mounting plane, or symmetry axis; see `positioning.md` for part-type origin defaults.
- **Order operations so fragile steps come last and failures localize.** Base solid → major additions → subtractive features → shell → through-wall holes → fillets and chamfers last. Fillets are the most failure-prone operation and every boolean invalidates selectors, so postpone them. Structure the source so each feature is a named step — a per-feature function or a distinct intermediate variable — so a failed operation points at exactly one feature and a parameter change touches one obvious place.
- **Overshoot boolean tools.** Extend cutting tools past the faces they enter and exit; for through-cuts, go roughly 1 mm beyond both faces. Coincident or coplanar tool/target faces are a classic kernel failure. Cut repeated or patterned features in one combined operation.
- **Sanity-check proportions before generating.** Compare the expected bounding box against the real-world object, wall thickness against overall size, and feature positions against edges and neighboring features. Order-of-magnitude and collision errors pass geometric validation but fail visual review.

## Topology stack

Think in this order:

```text
Vertex → Edge → Wire → Face → Shell → Solid → Compound
```

For assemblies, use these repo topology terms consistently:

- **Occurrence**: a placed node in the assembly tree. An occurrence has a parent, transform, path, and user-facing role such as `lid` or `m3_screw:front_left`.
- **Shape**: an exported geometry/body inside an occurrence. Shape rows own topology; faces and edges belong to a shape, and the shape belongs to an occurrence.
- **Face/edge**: selectable topology owned by a shape. Do not assume arbitrary faces or edges have persistent intent labels; inspect them by occurrence, shape, ordinal, surface/curve type, and measured geometry.

When inspecting topology, follow `assembly occurrence -> shape/body -> faces -> edges`. Every face/edge row should be traceable through both `occurrenceId` and `shapeId`.

For normal STEP output, return one of:

- a valid `Solid`
- a compound of valid solids
- a labeled assembly compound

Avoid returning loose wires, open faces, or construction surfaces unless the user explicitly requested them.

## Parameters first

Put meaningful dimensions in named variables:

```python
width = 80.0
depth = 50.0
thickness = 6.0
hole_diameter = 4.5
hole_offset_x = 30.0
hole_offset_y = 17.5
```

Avoid burying important numbers inside geometry calls.

## Coordinate system

Declare or comment the convention:

```text
Origin: center of primary part or chosen mating datum
XY: main base/sketch plane
+Z: up/extrusion direction
```

Use `Location`, `Plane`, and `Axis` intentionally. For positioning-sensitive tasks and source-level assembly relationships, read `positioning.md`.

## Builder contexts

Use the context that matches the geometry:

```python
with BuildLine() as path:
    ...

with BuildSketch() as profile:
    ...

with BuildPart() as part:
    ...
```

Typical flow:

```text
curves/paths → sketches/profiles → solids/features → labels → STEP
```

## Selection practices

Avoid fragile topology order when possible. Select by:

- axis or normal
- location or bounding position
- plane grouping
- feature intent
- stable construction plane
- inspected local selector ref for downstream validation

For source operations, prefer robust selectors such as top/bottom by axis or position rather than arbitrary list indexes.


## Assemblies and positioning

For assemblies, keep this file focused on BREP modeling patterns and labels. Use `positioning.md` as the single source of truth for:

- part-local coordinate conventions
- when to use `cadgen.assembly.AssemblyHelper`, build123d joints, or explicit `Location` transforms
- `connect_to()` behavior
- CLI `inspect align` as read-only selector-pair alignment validation
- frame, measure, and positioning report expectations

## Labels and assemblies

Label every exported part and assembly child with native build123d labels. Prefer concise intent labels through `cadgen.assembly` helpers:

```python
from cadgen.assembly import AssemblyHelper, label_shape

asm = AssemblyHelper("electronics_enclosure")
base = asm.add(make_base(), "base")
lid = asm.add(make_lid(), "lid")

boss = label_shape(Cylinder(radius=3.0, height=12.0), "m3_boss", "front_left")
```

Do not prefix labels with topology categories like assembly, component, feature, datum, mate, or hardware. The assembly tree and topology inspection already expose those structural categories. Use labels for the intent topology cannot reliably infer: role, placement, interface, repetition, or mating purpose. Feature labels survive STEP export best when the feature remains a labeled child shape in a `Compound`; boolean-subtracted or fused feature history should be represented by source parameters, named datums, and validation refs instead of assumed persistent feature labels.

Label for inspection:

- Label the root assembly.
- Label every exported part, subassembly/module, and repeated component occurrence.
- Use occurrence labels for assembly role and placement, especially repeated parts: `m3_screw:front_left`, `m3_screw:rear_right`.
- Use shape labels for retained exported geometry/body roles where useful.
- Use feature/datum labels only when that geometry remains exported as a child shape.
- Use named mate datums for source-level positioning intent, then validate the exported STEP topology and occurrence frames.

Occurrence and shape labels are exported through STEP names and surfaced in `STEP_topology` when available. The viewer uses occurrence labels for assembly/tree references and shape labels for shape references. Faces and edges inherit their context from `occurrenceId` and `shapeId`; do not promise persistent face/edge intent labels unless explicit tested support exists.

For repeated parts, keep occurrence labels, transforms, or joint connections explicit and inspect frames/positioning after generation.

## Colour

Two rules, both of which fail silently — no error, just a model that looks wrong.

**Channels are LINEAR RGB, not sRGB.** The renderer converts them to sRGB on the
way to the screen, so `Color(0.5, 0.5, 0.5)` displays as roughly `#BCBCBC`, not
`#808080`. Picking channel values off a hex palette by eye gives a washed-out,
desaturated model. Author with `cadgen.srgb()`, which takes the hex you want to
see:

```python
from cadgen import srgb

body.color = srgb("#2E3742")
glass.color = srgb("#38414D", 0.42)   # with alpha
```

**Colour on a group compound is ignored.** Only *leaf* occurrences carry colour
into the render package, so a colour set on a `Compound` that has children never
reaches the screen. It does reach the STEP file's XCAF label, which is why this
looks like it worked if you only check the STEP. Colour every leaf.

## Rotating a plane

`Plane.rotated()` composes its matrix in **WORLD axes, not the plane's own**.
On a plane whose axes are not the global ones this is the single most expensive
trap in the library, because the result is a valid solid of the wrong shape.

For a spanwise aerofoil section — `x_dir=(-1,0,0)`, `z_dir=(0,1,0)`, i.e. local
+x rearward and the normal along +Y — `plane.rotated((0, 0, twist))` reads like
a pitch and is actually a **yaw about world Z**. Measured on a 200 mm chord: a
20 deg "twist" put the trailing edge at `(812.0, -68.4, 99.4)` when it should
be at `(812.1, 0.0, 168.4)`. The section slid 68 mm sideways out of its own
spanwise station and rose nothing.

Nothing downstream catches it. The loft succeeds, the solid is closed,
watertight and free of self-intersections, and `scripts/inspect refs --facts`
passes it. Only looking at a render finds it.

Build the frame from explicit direction vectors instead:

```python
# incidence about the span axis, then yaw the whole frame
t, s = math.radians(twist_deg), math.radians(sweep_deg)
x_dir  = Vector(-math.cos(t) * math.cos(s), -math.cos(t) * math.sin(s), math.sin(t))
normal = Vector(-math.sin(s), math.cos(s), 0.0)
plane = Plane(origin=Vector(*origin), x_dir=x_dir, z_dir=normal)
```

The same applies to rolling a section about a swept member's own axis: use a
Rodrigues rotation about that axis rather than `Plane.rotated()`.

## Validity is not positive volume

`Shape.is_valid` (and `BRepCheck_Analyzer`) can return **True for a shell with a
large negative volume** — an inverted orientation. Such a body exports and
renders as a hole in the world. Check both:

```python
def is_valid_shape(shape):
    return (shape is not None
            and BRepCheck_Analyzer(shape.wrapped).IsValid()
            and shape.volume > 0.0)
```

Related: a boolean can leave a body that is geometrically right but
topologically invalid — correct bounds and volume, one bad face. It survives
until the next boolean, which then fails with `Null TopoDS_Shape object` from a
call nowhere near the cause. `ShapeFix_Shape` repairs many of these; gate every
boolean result rather than trusting the last operation.

`scripts/inspect validate` runs both of these gates plus closure and
self-intersection over every occurrence, so this does not have to be hand-rolled
per model. Note it measures volume **per solid**: an inverted member inside a
compound cancels against a sound one, so anything reading a compound's aggregate
volume sees nothing wrong.

## A revolve puts its seam at +X

A 360-degree `revolve` leaves a seam edge where its profile started, and
sketching on `Plane.XZ` places that seam at **+X**. If the presentation camera
looks down +X, every revolved casting renders with a thin panel line down its
visible face — on parts whose whole point is a smooth, sealed surface.

This is not limited to `revolve`: a plain `Cylinder` primitive seams at +X too,
verified with a marker probe. Any large smooth camera-facing cylinder is
affected.

Rotate the finished body about Z so the seam lands away from the camera. Two
cautions:

- A body carrying discrete features (a bolt ring, a stud circle) must be rotated
  by a whole number of feature pitches, or left alone and its *prototype*
  rotated instead — `bolt_ring`-style helpers only translate their prototype, so
  seam-hiding the prototype does not move the ring.
- A body offset from the origin must be rotated about **its own** axis: build it
  at the origin, rotate, then translate. Rotating in place about global Z flies
  it across the model.

Prove the fix with two renders — the seam absent from the camera face **and**
present on the far side. Without the second render you cannot tell a hidden seam
from one that was never visible at that angle.

## Fillet retry ladders degrade silently

The `pipe()`-style retry ladder (`[bend, .7, .5, .3, 20]` around
`FilletPolyline`) exists for a good reason: one oversized corner otherwise kills
an entire build with `BRep_API: command not done`. But it converts a hard
failure into an invisible cosmetic regression.

Where a profile cannot accept the nominal radius, the ladder silently falls back
— a 6 mm rim fillet became ~2 mm on a 660 mm-diameter flange, which tessellates
as a visible sawtooth. The build reports success; only a render cropped to ~5x
shows it.

Do not rely on the ladder for cosmetic radii. Reshape the profile so the
intended radius genuinely fits (a knife-edged wafer cannot take any fillet;
merge it into its neighbour), then verify by cropping the render.

## Common failure modes

- Fillet radius larger than local edge geometry.
- Open sketch profile produces invalid or missing face.
- A loft whose SECTION WIRE self-intersects. `make_face` accepts a
  self-intersecting periodic spline and reports a valid, positive-area face, so
  each station looks fine in isolation; the loft then fails on whichever
  adjacent pair is worst. Bisect by lofting adjacent pairs to find the station.
  Common cause: two points straddling a crease offset along the corner's
  TANGENT lines rather than placed on the curve, so the outline doubles back.
- Smooth `loft()` failing with `BRep_API: command not done` even though every
  section is individually valid and they all share one edge count. Try
  `loft(..., ruled=True)`; with densely spaced sections the result is visually
  equivalent.
- `solid += helper()` where the helper returns a *list*: the accumulator becomes
  a `ShapeList`, and the failure surfaces much later as an anytree
  `Cannot add non-node object` from inside `Compound(children=...)`.
- Face selector changes after a boolean or fillet.
- Part origin is arbitrary and later alignment checks become ambiguous.
- Source-level joints are treated as if they were persistent STEP constraints rather than one-time source placement operations.
- Joint labels are missing, duplicated, or attached to the wrong local datum.
- `.connect_to()` fixes the wrong side of the relationship, moving the part intended to remain fixed.

Use `repair-loop.md` when generation or validation fails.
