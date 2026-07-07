# CAD Viewer renderer consolidation

Status/handoff doc for unifying the **implicit/SDF** viewer with the
**mesh (STEP/STL/3MF/GLB/URDF/gcode)** viewer. Goal: one shared viewport
shell + clean interfaces, optimizing for code reuse, so global viewer
features are added once and work for every render format.

The mesh viewer (`CadViewer`) is the **reference implementation**; the
implicit viewer is being brought in line with it (not vice-versa).

## Branch / layout

Work lives on `claude/viewer-renderer-consolidation-ax9vix`, **rebased onto
`develop`** (symlink layout: `viewer/packages/{cadjs,implicitjs}` →
`../../packages/*`, so edits to `packages/*` source are live in the viewer
build). Do not develop against a `main`-derived copy layout — source edits
there are shadowed by the checked-in bundle copies.

Local test resolution (gitignored, recreate if missing): symlink each
package's deps from the installed viewer tree, e.g.
`packages/cadjs/node_modules/{three,gifenc,implicitjs}` and
`packages/implicitjs/node_modules/{three,gifenc,playwright}`. Without these,
standalone `npm --prefix packages/* test` fails to resolve `three`.

**Gotcha (bites first, hard):** the `packages/cadjs/node_modules/implicitjs`
symlink is the `file:../implicitjs` dep npm would normally create. It is
gitignored, so a fresh checkout of this branch is missing it — and because
`cadjs` now imports `implicitjs` (Phase 0 dedup), its absence makes **both**
`npm --prefix viewer run build` **and** `npm --prefix viewer run test` fail
(6 red tests, ~285/388 collected) with
`ERR_MODULE_NOT_FOUND: Cannot find package 'implicitjs' imported from
packages/cadjs/src/**`. It is not limited to standalone package tests. Recreate:
`ln -s ../../implicitjs packages/cadjs/node_modules/implicitjs`.

## Verification commands

- `npm --prefix viewer run build` — primary green signal (vite bundles everything)
- `npm --prefix viewer run test` — 384 pass / 4 skip
- `npm --prefix packages/cadjs test` — needs the node_modules test-symlinks above
- `npm --prefix packages/implicitjs test`
- **Visual (needed for Phases 1–3):** drive the running viewer in a browser and
  screenshot a mesh model and an implicit model before/after each change.
  Unit tests do NOT exercise 3D rendering, and the implicit↔mesh convergence
  changes real framing/render behavior. Launch via the `cad-viewer` skill and
  point `?dir=` at the repo `models/` root.
  - Fixtures now exist (the old "no implicit fixture yet" note is stale): dozens
    of `models/implicits/*.implicit.js` (e.g. small `parametric-pulse.implicit.js`).
    For a fast **mesh** comparison use a direct mesh such as
    `models/fun/miniature_spiral_staircase_highres.glb` (or any `*.stl`) — a raw
    `*.step` triggers slow on-demand artifact generation and can sit on
    "building…" for a while, which is a backend-generation delay, not a render bug.
  - Headless recipe: playwright is vendored at
    `packages/implicitjs/node_modules/playwright`; against the running dev viewer,
    `goto(<base>?dir=<models>&file=implicits/<name>.implicit.js)`, wait for
    `canvas`, ~9s for shader-compile + auto-fit, then `page.screenshot`. The mesh
    renderer has no `preserveDrawingBuffer`, so in-page canvas readback reads
    blank — trust the composited `page.screenshot`, not `drawImage`/`toDataURL`.

## Done (landed on this branch)

1. **Shared viewport camera kit** — `viewer/src/client/components/viewer/viewportCameraKit.js`.
   Extracted the renderer-agnostic camera/keyboard-orbit/view-plane/orbit/easing/
   frame-inset helpers + constants (the canonical `CadViewer` implementations).
   Both `CadViewer.js` and `ImplicitCadViewer.js` now import from it; their local
   copies were deleted. Behavior-preserving. The implicit runtime object now
   carries `runtime.THREE` to match the mesh runtime shape the kit helpers expect.

2. **`cadjs` depends on `implicitjs`; `camera.js` deduped** —
   `packages/cadjs/src/common/camera.js` was byte-identical to
   `implicitjs/common/camera.js` and now re-exports it. Dependency flows
   `cadjs → implicitjs` only. `packages/cadjs/src/lib/packageBoundary.test.mjs`
   was reversed to assert this direction and forbid `implicitjs → cadjs`.

3. **Single install** — `cadjs/implicit/*` re-export layer
   (`packages/cadjs/src/implicit/{render,model,loader,graphicsSettings,export,parameters}.js`,
   with explicit `exports` keys in `packages/cadjs/package.json` so both vite and
   Node resolve extension-less). All viewer imports moved from `implicitjs/*` to
   `cadjs/implicit/*`; `implicitjs` dropped from `viewer/package.json` (arrives
   transitively via cadjs). `AGENTS.md` updated for the new dependency direction.

4. **Phase 2 (partial) — implicit imperative-handle parity.**
   `ImplicitCadViewer`'s `useImperativeHandle` now exposes `resetZoom`,
   `zoomToFit`, and `zoomToFitSelection` (previously only
   `captureScreenshot/getPerspective/setPerspective/focusViewPreset`), matching
   the `CadViewer` ref contract. All three map to the existing `runAutoZoom`
   (`{ force: true }`, current view direction preserved — mirrors CadViewer's
   `resetZoomBaseline` fit); implicit has no sub-part selection, so
   `zoomToFitSelection` fits the whole model rather than no-oping.
   **Reachability caveat (discovered while doing this):** the plan's "these
   calls silently no-op on implicit" is only half the story — the shared viewer
   context menu that owns Reset Zoom / Zoom to fit **never opens for implicit at
   all**. `CadWorkspace.openGlobalViewerContextMenu` early-returns `null` when
   `!isStepView` (`isStepView = sourceFormat === RENDER_FORMAT.STEP`), and
   `ImplicitCadViewer` emits no right-click/context event (DXF and plain meshes
   are likewise menuless — the camera-action menu is STEP-only today). So these
   new methods are correct *contract parity / groundwork* but are not yet
   user-reachable. Wiring them up = emit a background right-click from the
   implicit (and mesh/DXF) viewer + open a minimal camera-action menu for
   non-STEP formats; that is a cross-format capability unlock, tracked under
   Phase 5, not a one-line fix.

## Remaining work

### Phase 1 — SceneBackend seam (the core; makes the two renderers stop diverging)

The only genuine rendering difference between the two viewers is one line in
the frame loop:

```
// mesh
renderer.render(scene, camera)
// implicit
updateImplicitCadMaterialUniforms(material, camera, w, h)   // feed camera as uniforms
renderer.render(shaderScene.scene, screenCamera)            // fullscreen quad + ortho screen-camera
```

Everything else the implicit component does (renderer/controls setup, RAF loop,
interaction/idle pixel-ratio quality, keyboard orbit, view planes, frame insets,
screenshot, perspective, resize, auto-zoom) is a re-implementation of what
`viewer/src/client/components/viewer/hooks/useViewerRuntime.js` already does for
the mesh path.

Plan:
- Define a `SceneBackend` interface: `attach(runtime)`, `renderFrame(runtime)`,
  `updateModel(runtime, model, opts)`, `getBounds()`, `dispose()`, plus a
  `capabilities` flag set (picking, displayModes, projectionToggle, clipPlane,
  explodedView, drawingOverlay, grid, lighting).
- Generalize `useViewerRuntime` to take a backend: its hardwired lights
  (`useViewerRuntime.js:179-223`) / scene groups (`225-236`) / renderer
  tone-mapping+shadow config (`157-165`) / grid / background and the
  `renderer.render(scene, camera)` call (the single seam, `line 367`) become the
  **mesh backend**'s `attach`/`renderFrame`. The `CadViewer` → `useViewerRuntime`
  call site (`CadViewer.js:2731-2782`) injects ~55 params, but **only ~8 are
  actually `viewportCameraKit` helpers** that can be imported directly
  (`stepKeyboardOrbit`, `getActiveViewPlaneFaceId`, `clearKeyboardOrbitState`,
  `isTrackpadLikeWheelEvent`, `getKeyboardOrbitCommand`, `getKeyboardOrbitAxes`,
  `applyOrbitDelta`, `KEYBOARD_ORBIT_NUDGE_RAD`). The rest are **CadViewer-local**
  (`stepCameraTransition`/`cancelCameraTransition` transition engine,
  `getPixelRatioCap`, `applyCameraFrameInsets`, `clearSceneGroup`,
  `disposeSceneObject`, `disposeTexture`, `applySceneBackground`,
  `updateGridHelper`, and unmemoized closures `emitPerspectiveChange`/
  `syncViewPlaneOrientation`/`applyInitialPerspective`) — those must be *extracted*
  into shared modules or the backend, not merely re-pointed at the kit.
- Write an **implicit backend**: `attach` builds `createImplicitCadFullscreenScene`
  (from `cadjs/implicit/render`); `renderFrame` does the uniforms + quad two-liner;
  capabilities mostly `false`; implicit-only extras (shader `compileAsync` warmup,
  graphics-settings-driven resolution scale) live here.
- Rewrite `ImplicitCadViewer.js` to mount `useViewerRuntime` with the implicit
  backend, deleting its bespoke renderer/controls/RAF/frame-inset/screenshot/
  perspective code (~600–800 lines). NOTE the framing technique differs today:
  mesh uses `camera.setViewOffset` for frame insets; implicit shears
  `projectionMatrix.elements[8]/[9]`. Converge on the mesh technique and verify
  the raymarch framing visually (the shader reads `projectionMatrixInverse` via
  `updateImplicitCadMaterialUniforms`, so `setViewOffset` should carry through).

Concrete gotchas for the implicit backend (verified against the code map):
- `createImplicitCadFullscreenScene(THREE, model)` returns
  `{ scene, material, quad, shaderKey, dispose }` — the mesh key is **`quad`, not
  `mesh`**, and there is **no `screenCamera` in the return**. The backend must
  create/own its own `new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1)` for the
  `renderer.render(shaderScene.scene, screenCamera)` call; the perspective camera
  exists only to feed uniforms.
- `updateImplicitCadMaterialUniforms(material, camera, w, h)` is **not THREE-first**
  (unlike the factories); it reads `camera.position/matrixWorld/projectionMatrixInverse`
  and sets `uResolution`. Easy to mis-wire.
- **No warmup export.** The `renderer.compileAsync(scene, screenCamera)` gate that
  sets `shaderSceneReady` (prevents a multi-second first-frame tab freeze) lives in
  `ImplicitCadViewer.armImplicitShaderCompile` and must move into the backend.
- **No in-place shader swap:** when `shaderKey` changes (model/uniform-signature
  change) the backend must `dispose()` and recreate the scene, then re-arm warmup.
- The mesh `runtimeRef` publishes ~60 fields the rest of `CadViewer` reads
  (`useViewerRuntime.js:557-624`: 8 lights, 6 groups, `facePickMesh`/`edgePickLines`/
  `vertexPickPoints`, `modelBounds`, thresholds…). The mesh backend's `attach` must
  still populate all of these on `runtime`, or large swaths of `CadViewer` break —
  and unit tests won't catch it (no 3D rendering in tests), so this step needs
  real visual verification of orbit/pick/edges/grid across themes.
- `getScreenSpaceLineMaterialCount` already peeks
  `runtime.cadScene.runtime.screenSpaceLineMaterials` — a *second* runtime concept
  layered on top. The `SceneBackend` interface should subsume this, not add a third.

### Phase 2 — unify perspective + close imperative-API gaps

- Converge the perspective snapshot format: implicit uses a separate
  `IMPLICIT_CAMERA_VERSION` payload (`perspectiveSnapshot`/`applyPerspectiveSnapshot`
  in `ImplicitCadViewer.js`); mesh uses `cadjs/lib/perspective` +
  `readScopedPerspectiveSnapshot`. Move to the mesh format so `getPerspective`/
  `setPerspective` payloads are interchangeable; version-migrate stored snapshots
  in `localStorage` so old implicit state degrades gracefully.
- ~~Implement `resetZoom`/`zoomToFit`/`zoomToFitSelection` for the implicit
  viewer.~~ **Done (ref-contract parity)** — see Done #4. Note the reachability
  caveat there: the context menu that would call them is STEP-only today, so the
  *user-facing* half of this gap is really a Phase 5 cross-format capability
  unlock (emit a right-click + open a camera-action menu for non-STEP formats),
  not just the ref methods.

### Phase 3 — collapse to one component

Fold `ImplicitCadViewer` into a single `CadViewer` that selects the backend by
`renderFormat`. `CadRenderPane.js` stops branching mesh-vs-implicit (keep DXF-2D
routing to `DxfViewer`). Keep the FileSheet inspectors format-specific
(`ImplicitFileSheet` vs `StepFileSheet` are legitimately different).

### Phase 4b — `themeSettings.js` / `displaySettings.js` dedup

Not a copy-paste like `camera.js`. `cadjs/common/themeSettings.js` (~1690 lines)
extracted the CAD edge constants into `cadjs/common/displaySettings.js`;
`implicitjs/common/themeSettings.js` (~1717 lines) still inlines them and adds
`resolveThemeDisplayEdgeSettings` / `resolveThemeSettingsDisplayEdgeSettings` /
`CAD_EDGE_*`. To unify with `implicitjs` as the source (so `cadjs` re-exports):
bring `implicitjs`'s copy up to the cadjs content and reconcile where the edge
constants live across both (either move the shared `displaySettings` foundation
into `implicitjs`, or keep edge constants inlined in `implicitjs` and have
`cadjs/common/displaySettings.js` re-export them). Many cadjs modules import
`displaySettings`, so tread carefully and lean on the package unit tests.
Also fold the trimmed-subset duplicates `implicitjs/lib/viewer/{stageTheme,surfaceMaterials}.js`
(subsets of the cadjs versions) into shared color/theme-seed helpers.

### Phase 5 — progressive feature unlock (after the shared shell exists)

With the shell + capability flags, selectively enable for implicit where it
makes sense: projection toggle, view planes, grid/stage backdrop, clip-plane/
section, annotated screenshots. Each becomes a capability flip + a small backend
hook, not a re-implementation.

## Bundling / release note

The generated bundle copies (`skills/*/scripts/*/packages/*`, `plugins/*`, and
on `main` the `viewer/packages/*` copies) are regenerated from `packages/*`
source with `scripts/bundle/bundle.sh` (requires `rsync`). Run
`scripts/bundle/bundle.sh --check` before handoff and regenerate when a
production-output task requires it. Do not bump `plugins/cad/VERSION` during
development.

## Key file map

- `viewer/src/client/components/CadViewer.js` — mesh viewer (reference)
- `viewer/src/client/components/ImplicitCadViewer.js` — implicit viewer (to fold in)
- `viewer/src/client/components/viewer/hooks/useViewerRuntime.js` — the shell to generalize
- `viewer/src/client/components/viewer/viewportCameraKit.js` — shared camera helpers (new)
- `viewer/src/client/components/workbench/CadRenderPane.js` — format → viewer routing
- `packages/cadjs/src/implicit/*` — cadjs → implicitjs re-export layer (new)
- `packages/cadjs/src/common/camera.js` — re-exports `implicitjs/common/camera.js`
- `packages/implicitjs/src/lib/implicitCad/render.js` — implicit shader scene API
