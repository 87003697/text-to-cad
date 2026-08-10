# Robot descriptions as a first-class render type

Companion to `design/viewer-format-unification.md`, which folded STEP, DXF, the mesh
formats and implicit into one capability-driven shell (93 → 34 identity checks). URDF, SRDF
and SDF were carried along by that work but never audited on their own terms. This is that
audit, and the plan it produces.

Written 2026-08-09 against `claude/viewer-format-unification-u2-u5`.

## 0. The short version

The robot family is **structurally** in the shared stack — same `CadViewer`, same stage,
same theme, same camera, one registry row, its own `fileSessionState` slice — and
**functionally** the thinnest render type in the viewer. It is the only one with:

- no headless render path at all (the snapshot CLI rejects robot inputs outright),
- no export route,
- no structure panel, despite a URDF being a link tree,
- no display modes, no clip plane, no exploded view,
- a load that takes ~20× longer than a mesh of comparable size, all-or-nothing.

None of that is a bug in the sense of something broken. It is what "carried along" looks
like: every feature added since robots landed was added where someone was looking, and
nobody was looking at robots. The unification effort's own gate had **no robot fixture
until this branch** — six formats swept, and the seventh was the one nobody checked.

## 1. What the audit found

Measured on `models/robots/so101/so101.urdf` (13 link meshes, 16 MB of STL) against a
viewer serving the repo `models/` root, unless stated otherwise.

### 1.1 No headless render path

`skills/cad/scripts/snapshot/__main__.py` says it plainly: *"direct DXF/G-code/robot-
description inputs are unsupported"*. `resolveHeadlessJobKind` knows exactly two backends,
`implicit` and `mesh`. So there is no way to snapshot a robot, no way to produce an orbit
GIF of one, and no way for a skill or CI job to render a robot at all.

This is the largest gap and the one with the clearest shape: a robot resolves to mesh data
in the viewer already (`selectedUrdfPreview.meshData`), so the headless mesh backend could
render it if something assembled the robot first. The work is a resolver arm plus a
headless-side URDF assembly step, not a new render stack.

### 1.2 No export

`exportFormats: []`, and `viewer/server_py/backend.py` implements `generate_export`
(STEP family), `generate_dxf_export` and `generate_implicit_export` — nothing for robots.
An assembled robot is a mesh scene; STL/GLB/3MF export is the same operation it is for an
implicit. Users can export the *parts* of a robot only by opening each link mesh
individually.

### 1.3 No structure panel, though a URDF is a tree

A URDF is a link/joint tree — the direct analogue of a STEP assembly tree, and the reason
`parts`/`topology` exist as capabilities. The robot row declares `parts: false,
topology: false`, so the robot sheet has Joints, Motion and (for SDF) an SDF tab, but no
Tree: you cannot select a link, isolate it, hide it, or zoom to fit it. The DXF sheet grew
a Layers tab explicitly described in `fileSheetSections.js` as *"the drawing's own
STRUCTURE, the DXF analogue of STEP's Tree"*. The robot never got the same treatment,
despite having the most literal tree of the three.

Consequence, now visible: since this branch gave every format the Select tool, a robot
shows a Select button that can never select anything — not because select is inert for
robots in principle, but because no link is pickable.

### 1.4 No display modes, no clip, no exploded view

`displayModes: false, clip: false, exploded: false`. The robot sheet has no Display tab
(`fileSheetSections.js` gives `THEME_DISPLAY` to `step` only). A robot cannot be shown
wireframe or transparent, cannot be sectioned, and cannot be exploded — three things that
are natural on an assembly of rigid links and are implemented once, on mesh data the robot
already produces.

### 1.5 The load is all-or-nothing, and slow

`useCadAssets` fetches every link mesh through `loadRenderRobotMeshes` and only then sets
`ASSET_STATUS.READY`. Nothing renders until the last mesh lands. Measured, cold, on
localhost:

| | model visible | toolbar usable |
|---|---|---|
| STL (6.3 MB, one mesh) | ~0.2 s | **0.8 s** |
| URDF (16 MB, 13 meshes) | ~15.7 s | **15.7 s** |

For ~2.5× the bytes, ~20× the wait, with a static "Loading URDF robot…" card for the whole
of it. The loader *does* track progress — it sets `loading meshes 7/13` — but that stage
string only reaches the file-list status chip, never the viewport card the user is looking
at. Two fixes are available and independent: surface the stage the loader already reports,
and render links as they arrive instead of waiting for the set.

(This also forced the standing sweep's robot fixture to a 26 s window, against 9 s for
every other format.)

### 1.6 Framing

The robot fixture sweeps at 0.10 non-background coverage against 0.16–0.31 for every other
format — it is framed small. Not diagnosed; recorded because the DXF equivalent turned out
to be a real bug (a HATCH seed point inflating the bounds 35×) rather than a camera
problem, so the same suspicion applies to the robot's bounds rather than its fit.

### 1.7 What robots already share, and should not be "fixed"

Worth stating so the plan is not read as "robots are broken":

- One `CadViewer`, one stage, one theme, one camera kit, one zoom/fit stack.
- One registry row; `content: robot` and `assetKind: robot` are honoured everywhere.
- The shared alert builder (`buildViewerMeshAlert`) — including, as of this branch, the
  correct "confirm the file exists" resolution rather than a rebuild command.
- The shared `fileSessionState` `urdf` slice for per-file persistence.
- Orbit, screenshot, pan, draw and the viewport context menu, all as of this branch.
- `sceneScale: "urdf"` is a genuine capability, not a divergence: robots are authored in
  metres and CAD in millimetres, and the scale profile is one registry field.

### 1.8 The one capability robots have and nothing else does

`posePicker`. It is correctly a capability and correctly robot-only today. Note it will
interact with §2.1: if links become pickable, pose-picking and link-selection are two
things a click could mean, and the toolbar already has a mode concept (`tabToolMode`) to
disambiguate them.

## 2. Plan

Same discipline as the format-unification phases: each is independently shippable, each
states its gate, and none of them adds a parallel stack. Ordered by user-visible value
over effort.

### R0 — the robot is in the standing gate (DONE, this branch)

A `robots/so101/so101.urdf` fixture in `viewer/scripts/e2e-format-sweep.mjs`, asserting
the same things every other format is asserted on: not blank, no page errors, the whole
viewport tool cluster present and usable, and the viewport menu open with camera actions.
Every phase below gates on this staying green.

Note for anyone running it: robot fixtures need `git lfs checkout models/robots/so101`
first, and the robot's window is 26 s until R3 lands.

### R1 — links are parts

Declare `parts: true` for the robot row and publish the URDF link tree through the same
`stepTreeRoot` shape the STEP path uses, so the shared Tree section, selection, hide,
isolate and zoom-to-fit-selection all mount unchanged.

1. Build a tree from `urdfData` links/joints (one node per link, nested by joint parent)
   and feed it wherever `stepTreeRoot` is fed, gated on `parts` rather than on the STEP
   format — the gate is already capability-shaped after U3.
2. Tag each link's mesh with its link name as the part id so picking resolves.
3. Leave `topology: false`: a robot has no BREP faces or edges, and the registry test
   already asserts topology implies parts, not the reverse.
4. Decide the click contract against `posePicker` (§1.8) — recommended: pose-picking stays
   an explicit tool mode, so a plain click selects a link like it does everywhere else.

Gate: select, isolate, hide and zoom-to-fit a single link on `so101` and on a
multi-branch robot (`openarm-bimanual`); STEP tree behaviour pixel-unchanged; ratchet not
raised.

### R2 — display modes, clip and exploded for robots

With links as parts (R1), all three are capability flips plus whatever falls out:
`displayModes: true, clip: true, exploded: true`, and `THEME_DISPLAY` added to the robot
arm of `fileSheetSections.js`. Every implementation already operates on mesh data and part
records, which a robot has.

Exploded view is the interesting one: the hierarchical radial explode is defined over an
assembly tree, and a robot tree is a kinematic chain — an exploded robot should be
inspected before it is shipped, since "radially bloom the links" may or may not read well
on a serial arm.

Gate: each of the three modes on `so101`; a screenshot per mode recorded in the sweep's
output dir; STEP unchanged.

### R3 — progressive robot loading

Two independent changes, in value order:

1. **Surface the stage the loader already computes.** `urdfLoadStage` produces
   `loading meshes 7/13`; route it into the viewport loading label, not only the file-list
   chip. Small, and turns a 15 s blank wait into a progress readout.
2. **Render links as they arrive.** Publish `urdfState` incrementally — a link with its
   mesh loaded draws; a link without one is skipped — so the robot appears in pieces and
   completes, instead of appearing whole at the end. `ASSET_STATUS.READY` still waits for
   the full set, so nothing downstream changes its contract.

Gate: measured time-to-first-pixel and time-to-toolbar-usable on `so101`, reported against
the 15.7 s baseline in §1.5; the sweep's robot window drops back toward the 9 s the other
formats use.

### R4 — robots in the headless renderer

The largest piece, and the one that unblocks robot snapshots for skills and CI.

1. Teach `resolveHeadlessJobKind` a `robot` kind (it currently knows `implicit` and
   `mesh`), and the snapshot CLI to accept `.urdf`/`.srdf`/`.sdf` inputs.
2. Assemble the robot headlessly — the viewer's `selectedUrdfPreview` already turns
   `urdfData` + link meshes into ordinary mesh data with world transforms; that is the
   piece to share, and it is UI-free, so it belongs in `packages/cadjs`.
3. Feed the result to the existing headless mesh backend. Camera, appearance, projection,
   orbit and size profiles come for free.
4. Joint values are the robot's analogue of STEP `--params`: a `--joints` JSON, and the
   animated sweep that already exists for parameter animation gives robot motion GIFs.

Gate: `snapshot so101.urdf` produces a still and an orbit GIF; a posed render differs from
the rest pose; the CLI's rejection message stops naming robot descriptions as unsupported.

### R5 — export

`exportFormats: ["stl", "glb", "3mf"]` for the robot row, and a `generate_robot_export`
beside the implicit one in `viewer/server_py/backend.py`, exporting the assembled robot at
its current joint values. Depends on R4's shared assembly step; without it this would be a
second assembler.

Gate: export each format from the viewer; re-import the GLB and confirm the pose matches.

### R6 — framing

Diagnose §1.6 with the DXF precedent in mind: check the robot's published bounds for
outliers before touching the fit. A link with a degenerate or far-off-origin visual origin
would produce exactly this symptom.

## 3. Non-goals

- No second render stack. Everything above routes robots through the mesh path that
  already draws them.
- No BREP topology for robots. There is none to have.
- No change to the URDF/SRDF/SDF *authoring* skills or their validators — this is viewer
  parity only.
- No move of `posePicker`, joints or motion planning out of the robot sheet. Those are
  format-specific CONTENT, which the registry gates but does not absorb.

## 4. Standing verification

`viewer/scripts/e2e-format-sweep.mjs` with the robot fixture (R0), plus
`viewer/scripts/e2e-theme-conformance.mjs` if any phase touches shading. Each phase also
states its own gate above. The identity-check ratchet in
`tests/python/global/test_viewer_format_capability_policy.py` must not rise: R1 and R2 are
capability flips, and a phase that needs a robot identity check has found a missing
capability.
