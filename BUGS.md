# BUGS.md — text-to-cad repo issues hit during the chronograph build

Running log of repo bugs, unexpected behavior, and doc gaps found while
building `models/renders/moonwatch/`. Watch-model problems do not belong
here. Format per entry: what I was doing, exact command, exact error/wrong
output, workaround, blocked?, fixed?

---

## 1. `packages/cadjs` ESM cannot be loaded standalone from a lightweight worktree

- **Doing:** extracting the `cinematic` theme preset JSON to author a
  presentation render theme (`node -e "import('./packages/cadjs/src/common/themeSettings.js')..."`).
- **Error:** `Cannot find package 'implicitjs' imported from packages/cadjs/src/common/camera.js`,
  then `Cannot find package 'three' imported from packages/implicitjs/src/common/camera.js`.
- **Cause:** worktrees are intentionally lightweight (no `node_modules`), and
  `cadjs` resolves `implicitjs`/`three` as bare specifiers, so even a module
  of pure data constants (`themeSettings.js`) cannot be imported without a
  full install.
- **Workaround:** symlinked `packages/cadjs/node_modules/implicitjs -> ../../implicitjs`
  and `packages/{cadjs,implicitjs}/node_modules/three -> <main checkout>/packages/cadjs/node_modules/three`.
- **Blocked:** no (workaround in minutes). **Fixed:** no (logged only —
  non-blocking; arguably by design).

## 2. `CADGEN_WARM=1`: killing the CLI client does not cancel the in-daemon job

- **Doing:** first build of `finishing_sampler.step.py` was slow (my own
  O(n^2) boolean accumulation); I killed the client
  (`pkill -f "scripts/gen finishing_sampler"`) and relaunched with fixed
  source.
- **Wrong output:** the relaunched client sat at 0% CPU for minutes. The
  daemon (pid from `$TMPDIR/cadgen-daemon-*.log`) was still burning ~600%
  CPU on the *killed* client's job — requests are handled sequentially, so
  the new run silently queued behind a job whose requester was gone.
- **Workaround:** `kill -9 <daemon pid>` (socket + staleness handling
  respawn a fresh daemon transparently on the next call).
- **Suggestion:** the daemon should abort a job when its client disconnects.
- **Blocked:** ~10 min lost. **Fixed:** no (workaround only).
- **FIXED in root source (2026-08-07):** the daemon now runs a liveness
  watchdog thread per request. Clients half-close their write side after the
  request, so read-EOF is normal — the probe is an empty stdout chunk (a
  protocol no-op for every client) sent every 0.5 s under a shared send lock;
  a failed send means the requester is gone, and the daemon logs, unlinks its
  socket, and exits, so the orphaned job stops burning CPU and the next
  invocation spawns a fresh daemon (the client already handles a missing
  socket that way). Test: `test_d_client_disconnect_aborts_orphaned_job` in
  `tests/python/skills/cad/cadgen_daemon/test_daemon.py`.

## 3. Sub-mm finishing booleans: overlapping-tool networks are pathological (OCC, not a repo defect per se)

- **Doing:** perlage (overlapping 0.02 mm-deep spherical dimples) on a
  14×8 mm coupon for `models/renders/moonwatch/_finishing.py`.
- **Wrong output:** no error — `scripts/gen` sat in "Building geometry"
  indefinitely (>40 CPU-minutes for ~200 stamps; even ~60 stamps took
  minutes). Two escalating causes, both silent: (a) pairwise `a + b`
  accumulation of boolean tools is O(n²); (b) even in ONE multi-tool op,
  dimple spheres have ~15 mm radii, so every tool overlaps every other
  deep below the surface and OCC builds one giant intersection network.
- **Workaround (both applied):** batch all boolean tools into a single
  list-operand op, AND pre-clip each stamp to a small lens cap
  (`Sphere & Cylinder` prototype, translated copies) so tools are
  disjoint. 14×8 mm field: >40 CPU-min → 0.69 s.
- **Suggestion:** `scripts/gen` progress JSON could surface elapsed time
  per phase (it reports `ratio: 0.0` forever); a doc note in
  `references/build123d-modeling.md` about multi-tool list booleans would
  save others this cliff.
- **Blocked:** ~45 min lost. **Fixed:** in model helpers (no repo change).

## 4. `scripts/gen` prints nothing to stdout/stderr during long builds

- **Doing:** first `scripts/gen finishing_sampler.step.py` runs (issues 2/3).
- **Wrong output:** zero output for the entire run — no phase logging, no
  heartbeat; the only liveness signal is a hidden
  `__cadgen__/models/.<name>.generation.progress.json` (whose `ratio`
  stays 0.0 in the generate phase) plus `ps`. Made the hang look like a
  crash and cost several kill/retry cycles.
- **Workaround:** watch the progress JSON + process CPU by hand.
- **Blocked:** contributed to the ~45 min above. **Fixed:** no (logged).

## 5. Near-tangent boolean tools are dropped SILENTLY (OCC kernel via build123d)

- **Doing:** case cluster — flat crystal/crown/pusher domes built by intersecting
  huge near-tangent spheres (R≈1700 mm) with small revolves; also a crystal
  multi-tool subtract.
- **Wrong output:** no error, exit 0, `inspect validate` clean — but half a
  tool's material was silently not removed (pusher head half-vanished), and one
  subtract left a stray disjoint 21.6 mm³ solid floating inside the crystal.
  Classic silent-no-op/degenerate-geometry behavior at near-tangency; only
  visual snapshot review caught it.
- **Workaround:** avoid near-tangent booleans entirely — build such domes as a
  single revolved profile (RadiusArc in the profile), which is also crisper.
- **Blocked:** no (caught in builder self-review). **Fixed:** in model source.

## 6. Snapshot renderer shows transparent parts (alpha < 1 source colors) as milky-opaque

- **Doing:** case cluster snapshots; `crystal` has color alpha 0.16, sapphire
  0.14 (confirmed present in the artifact descriptor).
- **Wrong output:** in `scripts/snapshot` renders the crystal reads as a milky
  solid dome rather than glass; unclear whether the GLB bakes alpha and the
  snapshot material ignores it, or alpha is dropped earlier.
- **Workaround:** none yet; to be re-checked at whole-watch compose (may need
  `display.mode` tweaks or a transparent-materials fix).
- **Blocked:** not yet (cosmetic until final renders). **Fixed:** no.
- **Root cause (traced):** two independent alpha drops.
  1. `packages/cadgen/src/cadgen/_internal/glb.py add_material()` bakes the
     RGBA into `baseColorFactor` but never sets `alphaMode: "BLEND"`, and glTF
     defaults to OPAQUE → alpha ignored by conformant loaders. **Fixed in root
     source** (BLEND set when alpha < 1) — helps standalone GLB exports.
  2. The component-package compose path drops alpha entirely: descriptor
     occurrence override colors go through
     `packages/cadjs/src/lib/assembly/meshData.js linearRgbToHex()` (3
     channels only), and `lib/viewer/surfaceMaterials.js` derives opacity
     solely from theme/display-mode settings — there is no per-part opacity
     concept at all. A real fix means threading alpha through part records
     into per-material `transparent`/`opacity`; too invasive for this
     project's "minimal targeted fixes" rule.
- **Adopted workaround:** snapshot renders `--hide` the glass occurrences
  (crystal, caseback sapphire); optically defensible for macro shots.

## 7. Snapshot JSON jobs silently ignore unknown top-level keys (`hide` vs `selection.hide`)

- **Doing:** hiding the crystal in a `--job` render; wrote top-level
  `"hide": ["#o1.5"]` by analogy with the `--hide` CLI flag.
- **Wrong output:** no error, no warning — the job rendered normally with
  nothing hidden (two identical renders before the cause was found). The
  correct schema is `"selection": {"hide": [...]}`; the CLI flag maps to it
  internally (`merge_focus_hide_options`).
- **Suggestion:** reject or warn on unrecognized top-level job keys; the help
  text describes `--focus`/`--hide` flags but not the job-JSON field shape.
- **Blocked:** ~10 min. **Fixed:** no (workaround: use `selection.hide`).
- **Same trap, per-output variant (case lug fix, 2026-08-06):** a
  `"selection": {"hide": [...]}` object nested inside an `outputs[]` entry is
  ALSO silently ignored — `selection` is read only at job level
  (`__main__.py` `job.get("selection")`), and unknown per-output keys are
  dropped without warning, so the render completes with nothing hidden. To
  hide parts in one view of a multi-view job, split it into separate jobs in
  a `{"jobs": [...]}` array.
- **FIXED in root source (2026-08-07):** `resolve_render_job` and
  `normalize_common_job` now validate jobs and outputs against closed key
  schemas (`SUPPORTED_JOB_KEYS` / `SUPPORTED_OUTPUT_KEYS`). Top-level
  `hide`/`focus`/`refs` get a message naming the `selection` object shape; a
  per-output `selection` gets the split-into-jobs fix; any other unknown key
  is rejected with the supported set listed. Covered by four new tests in
  `tests/python/skills/cad/snapshot/test_cli.py`.

## OCC chamfer on dome/eye-cap tangent chains: silent fail, minutes-long churn, or segfault (bracelet)

- **Where found:** `models/renders/moonwatch/_bracelet.py` (flat three-link
  bracelet rows: gently domed top face tangent to knuckle-eye cap cylinders at
  both link ends).
- **Symptom:** `chamfer()` on the link top/bottom perimeter edges behaved three
  different ways depending only on the exact link width (taper step): silent
  failure (safe_chamfer returns unchanged), ~90 s per attempt CPU churn (366 s
  through the retry ladder for ONE link — one row cost 265 s), or a hard
  uncatchable SIGSEGV inside OCC. First full gen took 11 min and standalone
  builds segfaulted at reproducible-but-width-dependent links.
- **Amplifier:** `_finishing.safe_chamfer`'s 0.7x retry ladder multiplies the
  churn 4-5x before giving up, and gives no signal that it degraded/failed.
- **Workaround (adopted):** never 3D-chamfer edges belonging to a tangent chain.
  The bracelet links now carry the side bevel in the extruded/lofted SECTION
  (octagonal profile with built-in 45-degree bevels, `_plan_prism`) and only
  chamfer isolated flank arc edges. Gen dropped 11 min -> ~28 s.
- **Blocked:** no. **Fixed:** worked around in model source; the underlying
  fragility is OCC's; consider a max-attempt-time guard in `safe_chamfer`.

## step_export warns "Unknown Compound type, color not set" for uncolored group compounds

- **Where found:** every `scripts/gen` run of
  `models/renders/moonwatch/bracelet.step.py` (labeled assembly with
  `strap_12`/`strap_6`/`clasp` group compounds; colors on leaves only, per the
  documented rule that color on a group compound is ignored anyway).
- **Symptom:** `packages/cadgen/src/cadgen/step_export.py:379` emits
  `UserWarning: Unknown Compound type, color not set` for each intentionally
  uncolored group node, so the recommended color-the-leaves pattern always
  builds with warning noise.
- **Expected:** group compounds without colors are the documented normal case
  and should not warn.
- **Blocked:** no (cosmetic/noise). **Fixed:** no.

## models/renders/moonwatch/_finishing.py: `align=(None,None,None)` is not "centered"

Found by the movement-base builder (2026-08-06). In build123d,
`align=(None, None, None)` places primitives at their RAW OCC datum —
`Cylinder`/`Cone` base at z=0 (XY centered), `Box` corner at the origin —
while `_finishing.py` (and `finishing_sampler.step.py`) were written assuming
it means centered. Verified empirically:

- `Cylinder(1, 2, align=(None,None,None))` -> z [0, 2] (not [-1, 1]).
- `Box(2, 2, 2, align=(None,None,None))` -> [0,2]x[0,2]x[0,2].

Downstream effects in `_finishing.py` (all silently wrong, no errors):

- `slotted_screw`: the slot cut box is corner-origin, so the "slot" is an
  off-center notch buried at mid-head height (x [0, 1.2*d], y [0, w]); the
  head-top datum is +head_height/2, not 0; the shank is shifted up by
  head_height/2 and pokes ~0.13 through the dome as a stub; the rim chamfer
  edge selector never matches (selects at -head_height, actual -h/2).
- `jewel_countersink_cut`: the cone is half above the surface and its flare
  is inverted (wider at depth -> undercut, not a polished countersink).
- `jewel`: top at +thickness/2, not 0 (jeweled_bearing partly compensates).
- `perlage_cutter`: the lens-cap prototype is clipped to z >= 0 by the raw
  cylinder, so at the documented "surface at z=0" datum the stamps remove
  NOTHING. (The sampler coupon only shows perlage because its plate is also
  built corner-origin with its top at +0.6.)
- `geneva_stripes_cutter`: bands are corner-origin: the field is offset +y
  by span_y*0.65 and the cutting band sits ~+0.46..+0.53 above the
  documented z=0 surface (again accidentally compensated in the sampler).
- `train_wheel`: the crossing-out ring/spoke cutters span z [0, web+0.02]
  against a web extruded both=True (z [-web/2, +web/2]), so spoke windows
  are only cut through the TOP HALF; a membrane floor remains in every
  window.
- `pinion`: body spans z [0, length] (not mid-plane 0) and the leaf boxes
  are offset half a leaf-width tangentially.

`_mvt_base.py` works around all of these locally (centered primitives via
default align, corrective cones/slots/window-cutters layered on top of the
helper output) without editing `_finishing.py`. Proper fix: change
`_finishing.py` to use default (centered) alignment and re-verify the
sampler; other movement builders should audit any direct use of these
helpers at documented datums.

## `_bracelet.py` end link hit the same `align=(None,None,None)` corner-origin footgun (silent, shipped)

- **Where found:** `models/renders/moonwatch/_bracelet.py` `make_end_link`
  (2026-08-06, while giving the bracelet links crowned sections). Same root
  cause as the `_finishing.py` entry above: `align=(None, None, None)` is the
  RAW OCC datum (Box corner at origin), not "centered".
- **Symptom:** two silent geometry defects in the shipped bracelet model, both
  probe-confirmed on the pre-fix source:
  - the "hollow back" cutter `Pos(0, 23.05, 1.7) * Box(16.6, 3.7, 2.4,
    align=(None,None,None))` spanned x [0, 16.6], z [1.7, 4.1] — it hollowed
    ONLY the +X half and cut up through the top surface (material probe at
    (+4, 24.5, 3.0) = empty, (-4, 24.5, 3.0) = solid);
  - the groove-pair cutters sat with their corner ON the top surface and
    extended upward, so the three-link separation grooves removed nothing.
- **Fix:** switched both cutters to default (centered) alignment in the same
  change that crowned the links. The class of bug is already documented above;
  this entry records a second independent module that shipped with it —
  auditing other `align=(None,None,None)` uses across `models/` is warranted.
- **Blocked:** no. **Fixed:** in `_bracelet.py` (this entry's instance only).

## build123d 2D sketch algebra: pairwise `+` decays, CW polygons shatter the fuse, and `ShapeList & Sketch` is silently EMPTY (keyless builder)

- **Where found:** `models/renders/moonwatch/_mvt_keyless.py` lever/spring
  profiles (circle+quad capsule chains for the setting lever, yoke, setting
  lever spring).
- **Symptoms (all silent, exit 0, `inspect validate` clean):** parts extruded
  to NOTHING (a lever reduced to its pin+boss debris), or to 5-8 disjoint
  solid piles, or extruded DOWNWARD from the sketch plane. Three stacked
  causes, verified empirically:
  1. Pairwise 2D algebra decays: `Circle + Circle` returns a fused `Face`
     (not `Sketch`), and the NEXT `Face + Polygon` falls into raw shape fuse
     that returns an unregularized face pile; once any step yields a
     `ShapeList`, later `+` is Python list concatenation, not geometry.
  2. A CLOCKWISE-wound `Polygon(..., align=None)` fuses as a reversed face:
     the union "succeeds" but shatters into +Z/-Z mixed-normal fragments,
     and `extrude()` of that runs along the reversed normals (solids appear
     mirrored below the plane) as disconnected pieces.
  3. `ShapeList & Circle` (intersection used as a regularizing clip) returns
     an EMPTY ShapeList with no error, so the following extrude quietly
     produces a zero-volume part.
- **Workaround (adopted, same as `F.train_wheel`'s internal pattern):** build
  every 2D profile as ONE multi-operand list fuse `first + [rest...]` with all
  polygons wound CCW, and apply the `& Circle(clip)` regularizer exactly once,
  LAST. Never accumulate 2D unions pairwise, never `+` two clipped results.
- **Suggestion:** a note in `references/build123d-modeling.md` next to the
  existing multi-tool boolean guidance; possibly a lint for `Polygon` winding
  in helpers.
- **Blocked:** ~30 min across two debug rounds. **Fixed:** in model source.

## Per-component STEP/GLB export silently drops the color of bare-`Compound` leaves (cadgen)

- **Where found:** `models/renders/moonwatch/_bracelet.py` bracelet rebuild
  (2026-08-06). 25 of 57 leaf bodies (all boolean/chamfer chains that happened
  to return a bare `build123d.Compound` instead of `Part`) rendered without
  their assigned `.color` even though `part.color` was set and the assembly
  STEP looked correct.
- **Mechanism:** `packages/cadgen/src/cadgen/step_export.py` has two color
  paths. The assembly-tree path (`is_assembly=True`) colors every child label
  and is fine. But the per-shape path used when a leaf is exported ALONE (the
  component-GLB cache builds one doc per component) only recognizes
  `Part`/`Sketch`/`Curve` when picking the sub-shape explorer; any other
  `Compound` subtype hits `warnings.warn("Unknown Compound type, color not
  set")` and exports uncolored geometry. The warning is easy to miss (it
  deduplicates per callsite and interleaves with gen output), so the model
  ships with silently washed-out parts — here it erased the brushed-outer vs
  polished-center bracelet contrast that the source colors specify.
- **Workaround (adopted):** coerce every leaf to `Part` before assembling the
  labeled `Compound` (`Part(shape.wrapped)` + reattach `.color`), see
  `build_bracelet()`.
- **Suggestion:** in `_create_bin_xcaf_doc`, treat an unknown one-solid
  `Compound` like a `Part` (explore `TopAbs_SOLID`) instead of warning, or
  raise loudly; silent color loss on valid colored input is a data bug.
- **Blocked:** no; found while chasing weak finish contrast in renders.
- **FIXED in root source (2026-08-07):** `_create_bin_xcaf_doc` now explores a
  bare `Compound` leaf for its actual content (solids, then faces, then
  edges) and colors it like the recognized types; the "Unknown Compound type"
  warning is gone. Regression test:
  `test_colored_bare_compound_leaf_keeps_color_and_does_not_warn` in
  `tests/python/packages/cadgen/test_compound_assembly_generation.py`. This
  also removes the constant warning noise from the related "uncolored group
  compounds" entry above (same warn site).

## Mirrored `Polygon` points flip the face normal, so `extrude()` runs the OTHER way (build123d, silent misplaced boolean)

- **Where found:** `models/renders/moonwatch/_bracelet.py`
  `_corner_relief()` (2026-08-06): corner-relief pockets built from a point
  list mirrored with `[(-y, z) for y, z in pts]` for the opposite link end.
- **Symptom (silent, exit 0, `inspect validate` clean, 57 occurrences):**
  mirroring the 2D profile reverses its winding, which reverses the planar
  face normal, and `extrude(face, amount)` extrudes along the normal — so
  every mirrored-end pocket extruded in -X instead of +X. The cutter gouged a
  strip 0.7 mm AWAY from the intended corner (it ate the end link's left
  prong tail, and the far-recess corner pockets on center links landed inside
  the crown), while the intended fang was left uncut. Point-classifier
  probing (`BRepClass3d_SolidClassifier` on mirrored coordinates) was what
  exposed the asymmetry; renders alone were ambiguous.
- **Workaround (adopted):** when mirroring a profile, also reverse the point
  order (`[(-y, z) for y, z in reversed(pts)]`) so the winding — and the face
  normal — is preserved.
- **Related:** same root class as the CW-polygon entry above (winding decides
  normals decides extrude direction); this instance is about MIRRORED point
  lists specifically, which look innocent in review.
- **Blocked:** ~20 min. **Fixed:** in model source.

## OCC `chamfer` on blob-outline top rings: whole-ring fails, singles refuse concave junctions, grouped-after-neighbors SEGFAULTS

- **Where found:** `models/renders/moonwatch/_mvt_base.py` bridge anglage
  (2026-08-06, movement-base finishing pass). The bridges are extrusions of
  multi-circle union ("blob") profiles clipped to a disk.
- **Symptoms (probe-measured on plain extrusions, BEFORE any boolean):**
  - `chamfer(all_top_edges, length)` fails at EVERY width on the barrel
    bridge outline and only succeeds at ~0.10 on the train bridge / balance
    cock, so `_finishing.anglage_top`'s whole-list retry ladder shipped
    bridges with zero or ~0.10 anglage (the blind critic's "plain vertical
    extruded walls").
  - Single-edge `chamfer` raises catchable ValueError on any arc bounded by
    a concave circle-circle junction (most of the visually large arcs).
  - Single- or multi-edge `chamfer` on a body that already carries bevels on
    NEIGHBORING arcs can SEGFAULT the process (exit 139, uncatchable, killed
    `scripts/gen`). Reproduced on the train-bridge wheel-reveal cutout rim
    after 3 neighboring arcs were beveled.
- **Workaround (adopted):** never chamfer these rings; BAKE the 45-degree
  anglage into construction — extrude to `z_top - w`, then a tapered cap via
  `extrude(..., taper=45)`; when the draft prism itself fails (barrel
  outline: "BRepFill_TrimSurfaceTool ... incoherent intersection", and loft
  to `offset(prof, -w)` also fails), union per-circle `Cone(r, r-w, w)` caps
  clipped by the rim cone plus the inward-offset profile extruded through
  the band. Baked bevels also survive later booleans.
- **Suggestion:** extend the "no 3D fillet after big booleans" guidance: on
  multi-arc blob outlines, OCC chamfer is unreliable even BEFORE booleans,
  and sequential chamfering can hard-crash; prefer constructive bevels.
- **Blocked:** ~45 min. **Fixed:** in model source (`_bevel_extrude`).

## build123d 0.10: `Part(solid.wrapped)` reports `volume == 0`

- **Where found:** `models/renders/moonwatch/_mvt_base.py` stripe-shadow
  overlays (2026-08-06), splitting a multi-solid boolean result into one
  part per connected solid.
- **Symptom:** re-wrapping a `Solid`'s `TopoDS_Solid` as `Part(sol.wrapped)`
  yields a shape whose `.volume` is 0 (probe: `Box(1,1,1)` solid -> Part
  wrap -> volume 0). Any volume-based guard then silently discards real
  geometry — a `shadow.volume < 1e-6: shadow = None` check threw away the
  balance-cock overlay bands with no error.
- **Workaround:** use the `Solid` objects directly as labeled/colored
  compound children (Shape carries `label`/`color` fine), or fuse before
  measuring. Do not `Part(x.wrapped)` a bare solid.
- **Blocked:** ~15 min (bands present in `inspect validate` count yet
  invisible; traced via descriptor + volume probes). **Fixed:** in model
  source.

## OCC multi-tool subtract with overlapping bore + ring tools leaves junk solids

- **Where found:** `models/renders/moonwatch/_case.py` case-middle
  finishing (2026-08-06): one batched `body - (functional_cuts +
  flank_ring_cutters + grain_tools)` where the crown-tube seat bore
  cylinder overlaps a stack of thin circumferential V-ring cutters.
- **Symptom:** the single multi-tool `BRepAlgoAPI_Cut` returns a ShapeList
  of 5 solids instead of one: the main body, the crown-seat bore PLUG
  (volume ~18 mm^3 — material the bore tool should have removed, kept as a
  detached solid), and knife-edge slivers of the guard wall between
  adjacent ring cutters (~0.01 mm^3, detached skins). Every individual
  tool is a valid positive-volume solid; subtracting the same tools in two
  stages (functional cuts, then ring/grain cuts) yields one clean solid.
- **Workaround (adopted):** split heavily overlapping tool families into
  separate batched subtracts — still list-based multi-tool booleans, never
  pairwise accumulation. Downstream `Compound(children=...)` also fails
  loudly on the ShapeList ("not a subclass of NodeMixin"), which is how it
  surfaced.
- **Suggestion:** the "booleans over many tools: ONE operation" guidance
  needs a caveat: when tools overlap each other (a bore crossing a stack of
  near-tangent finishing cutters), OCC's multi-tool cut can emit wrong
  results; group tools so each batch is internally disjoint-ish.
- **Blocked:** ~20 min (typed probes per builder, then staged-subtract
  bisection). **Fixed:** in model source (two-stage subtract).

## 9. No per-part material properties in the component-GLB render path (missing feature; the project's aesthetic ceiling)

- **Doing:** whole-watch and movement blind A/B comparisons against professional
  macro photography (the moonwatch project's core acceptance loop).
- **Limitation:** the presentation theme applies ONE global
  roughness/metalness/clearcoat to every part; descriptor occurrence colors
  carry only RGB (see entry 6). There is no way to render brushed steel next
  to mirror-polished steel next to matte lacquer as *different material
  responses* — only albedo differs. Across ~20 fresh-context critic rounds,
  after all geometric finishing (anglage ribbons, V-groove striping, satin
  grain, snailing, perlage) was modeled and visible, every remaining loss
  verdict converged on the same sentence: "the metal has only one finish /
  uniform material response / reads as primed plastic."
- **Workarounds used:** geometric micro-texture (V-grooves at 0.14–0.2 mm
  pitch) + albedo deltas + bright overlay "ribbon" bodies for polished zones +
  raking key light + strong neutral HDRI. These moved every render
  substantially but cannot produce anisotropic specular contrast.
- **Suggestion (follow-up feature):** thread optional per-occurrence
  `roughness`/`metalness` (and finish the alpha channel from entry 6) from
  cadgen's descriptor into `packages/cadjs` part records and per-part
  materials; the snapshot runtime picks it up on rebundle. This is the single
  highest-leverage renderer change for photoreal CAD presentation.
- **Blocked:** the blind-A/B win condition, not the modeling. **Fixed:** no
  (out of minimal-fix scope; documented).

## `safe_chamfer`'s volume-only gate accepts BOP-self-intersecting chamfers that `inspect validate` then rejects

- **Where found:** `models/renders/moonwatch/_mvt_chrono.py` lever anglage
  (2026-08-07, chronograph-works cluster). `F.anglage_top` /
  `F.safe_chamfer` accept a chamfer result when `result.volume > 0`.
- **Symptom:** on some capsule-chain lever perimeters (reset lever, reset
  spring, brake lever) OCC `chamfer` at 0.14 returned a positive-volume solid
  whose skinny chamfer faces are BOP-faulty — `BRepAlgoAPI_Check` reports
  `BOPAlgo_SelfIntersect` + `BOPAlgo_TooSmallEdge` — so the retry ladder
  "succeeded" and shipped parts that `inspect validate` flags as
  `selfIntersecting` (3 failures). `BRepCheck_Analyzer.IsValid()` on the same
  solids is True, so simple topology validity checks do not catch it either;
  only the BOP check used by `cadgen.validity` does.
- **Workaround (adopted):** `_mvt_chrono._lever` wraps the ladder with its own
  `BRepAlgoAPI_Check` gate and steps the width down (0.7x) until the chamfer
  passes, else skips the anglage.
- **Suggestion:** `F.safe_chamfer`/`safe_fillet` should gate on the same BOP
  check `inspect validate` uses, not just `volume > 0` — silent acceptance
  here surfaces only at validation time with no pointer to the causing op.
- **Blocked:** ~15 min. **Fixed:** locally in `_mvt_chrono.py` (helper
  unchanged; other clusters using `anglage_top` on wavy outlines can hit it).

## `snailing_cutter` V-groove cuts can leave BOP-self-intersecting results when a groove wall runs tangent to the target's profile wall

- **Doing:** cutting circular Geneva striping (the `_mvt_base._bridge`
  pattern: `F.snailing_cutter(...)` rings about the movement center,
  intersected with a stripe band, subtracted from a blob-outline bridge) into
  the new chronograph coupling cock in
  `models/renders/moonwatch/_mvt_chrono.py` (2026-08-07).
- **Symptom:** the cut "succeeds" — one positive-volume solid,
  `BRepCheck_Analyzer.IsValid()` True — but `BRepAlgoAPI_Check` reports
  self-intersection, so `inspect validate` flags the part
  (`selfIntersecting`, 1 failure) with no pointer to the causing operation.
  Probe-bisected: the plain bevel extrude, jewel countersink and screw sink
  were all clean; adding the stripe cut alone flipped the part to
  BOP-faulty. The cock's outline circles sat at radii where a groove's wall
  cone ran tangent to the stripe-band inset wall (outline centers ~10.2–11.8
  from origin vs groove edge circles at 10.7 +/- 0.925, 12.6 +/- 0.925).
- **Workaround:** nudge the outline centers/radii by ~0.03 until the
  tangency breaks (`COUPLING_COCK_OUTLINE` comment records the tuned
  values); verify with `BRepAlgoAPI_Check` per part before shipping.
- **Suggestion:** same class as the `safe_chamfer` entry above — cutters
  built from `F.snailing_cutter` (and other tangency-prone V-groove tools)
  should be BOP-checked after the boolean by the shared vocabulary, or
  `inspect validate` should name the last boolean when a part fails.
- **Blocked:** ~20 min (bisecting which cut was faulty). **Fixed:** locally
  (geometry nudged; helper unchanged).

## OCC kernel operations are broadly fragile on dense periodic B-spline profile faces (taper extrude, wire offset, coincident-face fuse, ruled loft at sharp corners)

- **Doing:** replacing the moonwatch bridge outlines (blobbed circle chains)
  with smooth closed silhouettes — ONE periodic `Spline` fit through ~250
  Catmull-Rom samples per bridge, `make_face`, then the `_bridge` factory's
  bevel/stripe/ribbon machinery — in
  `models/renders/moonwatch/_mvt_base.py` / `_mvt_chrono.py` (2026-08-07).
- **Symptoms** (each probe-verified in isolation on build123d 0.10 / OCP
  7.9):
  1. `extrude(face, amount, taper=45)` (`LocOpe_DPrism`) throws on EVERY one
     of the six spline profiles — `BRepFill_TrimSurfaceTool::IntersectWith:
     incoherent intersection`, `NCollection_DataMap::Find`,
     `BRepFill_MultiLine: ValueOnFace` — although the same call succeeds on
     small hand-sampled spline faces (~40 pts).
  2. `offset(face, -d, kind=Kind.ARC)` (`BRepOffsetAPI_MakeOffset` via
     `Wire.offset_2d`) returns Null (`ValueError: Null TopoDS_Shape
     object`) for EVERY inward delta on the balance-cock profile while
     outward deltas work; barrel/train/pallet profiles offset fine.
  3. Fusing two individually valid solids that share a coincident
     spline-bounded planar face (straight-wall extrude + its 45-deg bevel
     cap, common face = the same spline profile) returns an EMPTY result —
     `+` succeeds, `volume == 0`, zero solids — on the barrel profile only;
     the same construction fused clean on the other five.
  4. `BRepOffsetAPI_ThruSections` (build123d `loft(..., ruled=True)`)
     between a profile and its inward offset builds but is
     `BRepCheck_Analyzer`-INVALID when the outer wire has sharp corners
     (the ruled surface folds where outer corner radius < offset delta).
- **Workarounds (adopted, in `_mvt_base.py`):** compute ALL profile offsets
  numerically on the dense sample loop (normal offset + drop points closer
  than |delta| to the source polyline — the classic offset-validity prune —
  + resample + light smoothing), never calling kernel offset; build the
  beveled body as ONE 3-section ruled loft (wall, wall, inward offset) so no
  coincident-face fuse exists; give outline traces explicit corner-rounding
  points at rim junctions; derive the polished-ribbon shell from the body's
  own cap translated vertically (a 45-deg cone translated up by d equals the
  cone grown horizontally by d) instead of lofting a second grown pair.
- **Suggestion:** treat `extrude(taper=)`, kernel wire offset and
  coincident-face fuses as unavailable for interpolated many-point spline
  profiles; prefer ruled lofts between numerically offset sections. (This
  also supersedes the previous entry's `COUPLING_COCK_OUTLINE` 0.03-nudge
  note — the blob outlines it tuned no longer exist.)
- **Blocked:** ~1.5 h across the four failures. **Fixed:** locally
  (helpers in `_mvt_base.py`; kernel behavior unchanged).

### Entry 9 addendum (final, 2026-08-07)

Per-part PBR materials + opacity WERE subsequently implemented (root sources,
entry 6/9 suggestion; commit "render pipeline: per-occurrence PBR material
overrides + opacity"). They transformed the renders — real glass, lacquer,
mirror-vs-brushed steel — and moved blind-critic verdicts from "clay toy" to
component-level finishing quibbles. Nine further whole-watch rounds still
lost against professional photographs of the actual reference watch; late
verdicts repeatedly named features that demonstrably exist in the geometry
(striping, anglage ribbons, domed jewels, slotted screws), indicating the
remaining delta is renderer-class: no anisotropic BSDFs, no path-traced
bounce/DOF/grain. A photoreal path-tracing backend (or export to one) is the
next real step for the blind-A/B-vs-photograph bar; the modeling side is no
longer the limiting factor.
