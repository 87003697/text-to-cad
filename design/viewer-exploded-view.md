# Viewer: exploded view redesign

Recorded 2026-07-06. Research + design proposal: how professional CAD tools
model exploded views, why the current viewer implementation feels wonky
against that bar, and a step-based redesign that fits this repo's
cadjs/viewer/snapshot architecture. No code changes yet; this document is the
deliverable of the research task.

## 1. Current implementation

### Data model

Exploded view is a **global display setting**, one flat object shared by the
viewer UI, per-file display state, and agent snapshot JSON jobs
(`DISPLAY_OPTION_KEYS` in `skills/cad/scripts/snapshot/__main__.py:77`):

```js
// packages/cadjs/src/common/displaySettings.js:75
exploded: {
  enabled: false,
  axis: "z",            // "x" | "y" | "z" | "radial"
  direction: "positive",
  spacing: 1.45,         // 0.25–4 multiplier on heuristic gaps
  depth: 1,              // 1–8 occurrence-tree grouping depth
  keepBaseGrounded: true,
  mergeCoplanar: false,
  autoFrame: true
}
```

There is no per-component or per-step state anywhere. Everything a user can
express about an explode is those eight globals.

### Solver

`packages/cadjs/src/lib/viewer/explodedView.js` is a pure-function solver:
`createExplodedViewRecordStates(THREE, records, bounds, settings)` returns
per-record `{direction, distance, translation, matrix}` states, and
`applyExplodedViewProgress` writes a translation-only
`record.explodedViewMatrix` that `displayRecordTransform.js` composes into
the render — no scene rebuild, cheap per-frame updates.

- **Grouping**: display records are grouped by occurrence-path prefix
  (`o1.2.3`) at `depth` levels below the common prefix, so sub-assemblies
  move as rigid groups at depth 1 and shatter progressively as depth grows.
- **Axis mode (x/y/z)**: groups are sorted by center along the chosen axis
  and serialized into a stack of "layers", re-spaced with gap heuristics
  (`minimumGap`, thickness fractions, `spacing` multiplier). Coplanar groups
  become **separate layers unless `mergeCoplanar` is on** — and it is off by
  default. The first layer can stay grounded.
- **Radial mode**: groups are pushed outward in the world-XY plane away from
  the model center, height preserved; groups sitting on the model's vertical
  axis have no radial direction, so they are fanned by golden-angle
  (`radialFanDirection`). A ground-lift pass keeps parts above the base.
- **Animation**: toggling runs a 1 s cubic-ease transition in
  `viewer/src/client/components/CadViewer.js`, interpolating from current
  translations when settings change mid-flight.

### UI

The controls live in a subsection of the theme/display settings popover
(`viewer/src/client/components/workbench/ThemeSettingsPopover.js:1649`):
an Enabled toggle, an Axis segmented control, Spacing and Depth sliders,
Merge levels / Ground base toggles, Reset. The skill docs describe the
feature as "an independent Explode toggle for animated vertical STEP
disassembly" (`skills/cad-viewer/references/viewer-features.md:22`).

### What it gets right

Worth preserving in any redesign:

- Pure solver in `packages/cadjs` (non-React), applied as a per-record
  matrix — no geometry duplication, works with the package/instancing work.
- Occurrence-group rigidity and the depth concept (sub-assemblies first).
- Animated transitions that interpolate from the current state.
- Keep-base-grounded and auto-frame as explicit toggles.
- A declarative JSON surface that agents can drive headlessly through
  snapshot jobs.

## 2. Why it feels wonky

The root cause is architectural, not tuning: **the explode is a single
global heuristic layout, while every professional tool models an exploded
view as an editable, ordered list of per-component moves.** Concretely:

1. **No per-component control.** You cannot say "these two turbopumps go
   out ±X, the nozzle goes down −Z, the gimbal goes up +Z". The only knobs
   are one axis for *everything* or a radial bloom for *everything*. The
   repo itself documents the workaround: `models/spacex/raptor2/
   raptor2_exploded.step.py` hand-authors a per-group offset table
   (`OFFSETS = {nozzle: (0,0,-650), ox_turbopump: (380,0,120), …}`) plus
   translucent guide rods — i.e. contributors rebuilt explode *steps* and
   *explode lines* as separate STEP models because the viewer cannot
   express them. Same pattern in `starship_exploded.step.py`,
   `merlin1d_exploded.step.py`, `falcon_heavy_exploded.step.py`.
2. **Default axis mode serializes coplanar parts.** With `mergeCoplanar:
   false` (the default), four bolts at the same height become four separate
   layers stacked apart vertically. Side-by-side geometry (gripper jaws,
   boosters) gets sheared into a column. The fix toggle exists but is
   buried and off.
3. **Non-physical directions.** Axis mode moves everything along one world
   axis regardless of the assembly's own structure. Radial mode scatters
   coaxial parts sideways by golden-angle — a gearbox explodes axially in
   any catalog drawing, never as a sideways scatter. Directions never come
   from the part's own placement, contacts, or symmetry axis.
4. **Slider semantics.** `spacing` re-solves the layout with different gap
   multipliers; it is not the "explode amount" scrub every viewer-style
   tool offers. There is no way to scrub assembled ↔ exploded; only the
   on/off animation. `depth` (1–8) is an occurrence-path concept with no
   visual affordance for what each notch will do.
5. **Magic-number layout.** Distances are products of tuned constants
   (0.85, 0.28, 0.6, 0.22, 0.35 …) of bounding radii — not editable, not
   in model units, not stable across models. Users cannot type "move this
   650 mm".
6. **Nothing persists as an artifact.** No named exploded views, no explode
   lines/trails, no step sequencing for assembly-order animation, nothing a
   drawing/snapshot can reference besides the eight globals.
7. **Discoverability.** A core assembly-review feature is a subsection of
   the *theme* settings popover, siblings with backdrop color and edge
   thickness.

## 3. How professional tools model exploded views

*(Research summary — placeholder, to be filled from the web-research pass:
SolidWorks / Onshape / Fusion 360 step model, manipulators, trails,
animation; viewer-style sliders in eDrawings / Autodesk Viewer /
model-viewer; auto-explode literature.)*

## 4. Proposed design

Three layers, replacing the current single-heuristic pipeline. The guiding
move is the one every professional tool made: **promote the exploded view
from a display setting to a document — an ordered list of explode steps —
and demote the current heuristic to a generator that seeds that document.**
This serves both viewer users (who get editing) and agents (who get a
declarative per-group format they already invent by hand in the
`*_exploded.step.py` fixtures).

### 4.1 Layer 1 — explode-step data model (cadjs)

A serializable `explodedView` document, per file, versioned:

```js
{
  version: 1,
  name: "default",             // named views later; one view first
  steps: [
    {
      id: "s1",
      type: "translate",        // "translate" | "rotate" | "radial"
      targets: ["o1.3", "o1.4"],  // occurrence paths; groups move rigidly
      axis: [0, 0, 1],            // unit vector in model space
      distance: 650,              // model units (mm), not multipliers
      // rotate steps: axis + origin + angleDeg
      // radial steps: center + per-target outward directions resolved at solve time
    },
    ...
  ],
  order: "simultaneous" | "sequential",  // animation scheduling
  trails: true                            // explode lines, auto-routed
}
```

Semantics:

- **Progress scrubbing is first-class.** The evaluator is
  `translationAtProgress(step, t)` for global `t ∈ [0,1]`; `simultaneous`
  scales all steps by `t`, `sequential` maps step *k* of *N* to the
  `[k/N, (k+1)/N)` slice (collapse animations replay assembly order in
  reverse, like SolidWorks/Fusion animate collapse). Output stays what it is
  today: a per-record `explodedViewMatrix` (now possibly rotation too), so
  the render path, package instancing, and snapshot runtime are untouched.
- **Distances are absolute.** Steps store model units so users can type
  "650" and agents can compute offsets from part sizes; the raptor2
  `OFFSETS` table maps 1:1 onto translate steps.
- **Targets are occurrence paths**, same ids the STEP tree and selection
  already use; a target that names a sub-assembly moves the whole subtree
  rigidly (today's depth-grouping behavior, but explicit and per-step).
- **Chained frames**: later steps see earlier steps' displaced positions
  when `sequential` (matches pro-tool step chaining); `simultaneous` is
  what the current implementation effectively does.

New module `packages/cadjs/src/lib/viewer/explodedViewSteps.js` (evaluator +
validation + (de)serialization), keeping `explodedView.js`'s
progress/animation helpers. cadjs stays non-React per repo rules.

### 4.2 Layer 2 — auto-explode becomes a step generator

The current solver's job survives, reframed: `generateExplodedViewSteps(
records, bounds, hints)` returns a step list instead of directly returning
record states. Same inputs (bounds + occurrence tree; we have no mates), but
with the heuristics upgraded where they are wonky today:

1. **Principal-axis detection instead of a hardcoded axis.** Pick the
   explode axis per scope from the occurrence-center distribution (dominant
   PCA axis of group centers, tie-broken toward model Z). The `axis`
   setting becomes an optional hint, not the only input.
2. **Coplanar groups never serialize.** Groups whose extents overlap along
   the explode axis stay one layer and separate *laterally* (outward from
   the axis, snapped to the nearest principal direction) — i.e. the current
   `mergeCoplanar` behavior becomes always-on, and lateral separation
   replaces golden-angle fanning. Bolt circles and side-by-side jaws
   explode outward or stay with their layer instead of stacking.
3. **Coaxial stacks explode axially.** Detect groups whose XY centers
   coincide with the stack axis (the planetary-gear / gearbox case) and
   keep them on-axis with axial gaps; never scatter them sideways.
4. **Sub-assembly recursion.** Depth *n* explodes: move depth-1 groups
   apart with large gaps, then recursively explode inside each group with
   smaller gaps along that group's own principal axis. Today depth just
   regroups at a finer level and re-solves globally, which loses the
   sub-assembly structure it worked out at depth 1.
5. **Non-overlap by construction along each explode direction** (sweep and
   re-space, as axis mode does today; radial/lateral moves get the same
   sweep along their own direction).

The generated steps are ordinary steps: the user can then delete, retarget,
re-axis, or renumber them. Auto-explode is a seeding action ("Auto
explode"), re-runnable, not a live mode that fights manual edits.

### 4.3 Layer 3 — viewer UX

Move explode out of the theme popover into a first-class **Explode mode**
(floating-toolbar entry, like Select/Draw):

- **Explode amount slider, 0–100%**, always visible in the mode — the
  universally-understood viewer control (eDrawings/Autodesk Viewer style),
  scrubbing evaluator progress. Replaces "spacing" as the primary slider;
  a small "gap scale" stays in advanced settings and simply scales
  generated step distances.
- **Auto explode button** with the axis/radial presets (hints to the
  generator), plus Reset/Collapse.
- **Step list panel** (in the file sheet next to the STEP tree): ordered
  steps with target names from occurrence `displayName`s, editable numeric
  distance, reorder, delete; selecting a step highlights its parts.
- **Direct manipulation**: with parts selected (existing
  `useCadWorkspaceSelection` + picking), show a translation gizmo (three.js
  `TransformControls`-style arrows snapped to model axes + the group's
  inferred axis); dragging creates or updates a step; typed distance in the
  step list for precision. This is the SolidWorks/Onshape/Fusion authoring
  gesture, feasible because targets/ids/selection already exist.
- **Explode trails**: optional faded lines from assembled to exploded
  position per moved group (the guide rods raptor2 fakes today), rendered
  through the existing line/edge runtime.
- **Persistence**: the explode document rides the existing per-file display
  state channel, and snapshot jobs accept `exploded: {steps: [...]}` (or
  `exploded: {auto: {...hints}}` for generated ones) through the same
  `DISPLAY_OPTION_KEYS` slot. Old-format settings normalize into an
  auto-explode hint object, so existing URLs/jobs keep working.

### 4.4 Compatibility and phasing

`normalizeExplodedViewSettings` keeps accepting the current shape and maps
it to `{auto: {axis, direction, gapScale, depth, keepBaseGrounded}}`;
`enabled: true` with no steps means "generate on demand". Suggested order:

1. **Phase 1 — model + evaluator + slider.** `explodedViewSteps.js`,
   generator returns steps, CadViewer evaluates steps, explode-amount
   slider in the current popover. Fixes scrubbing and slider semantics with
   no UI relocation. Old settings normalize forward.
2. **Phase 2 — generator quality.** Principal-axis detection, lateral
   separation for coplanar layers, coaxial handling, recursion
   (§4.2.1–5). Snapshot fixtures re-baselined once.
3. **Phase 3 — step list + persistence + snapshot steps.** File-sheet step
   panel, per-file persistence, `exploded.steps` in snapshot jobs; retire
   the hand-authored `*_exploded.step.py` pattern for new models (author an
   explode document next to the base model instead).
4. **Phase 4 — direct manipulation + trails.** Gizmo editing and explode
   lines.

Each phase is independently shippable; Phase 1+2 alone remove the main
"wonky" complaints (serialized coplanar parts, sideways gear scatter,
unscrubable explode, opaque spacing).

## 5. Sources

*(Placeholder — filled from the research pass.)*
