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

## Verification commands

- `npm --prefix viewer run build` — primary green signal (vite bundles everything)
- `npm --prefix viewer run test` — 384 pass / 4 skip
- `npm --prefix packages/cadjs test` — needs the node_modules test-symlinks above
- `npm --prefix packages/implicitjs test`
- **Visual (needed for Phases 1–3):** drive the running viewer in a browser and
  screenshot a mesh model and an implicit model before/after each change.
  Unit tests do NOT exercise 3D rendering, and the implicit↔mesh convergence
  changes real framing/render behavior. Launch via the `cad-viewer` skill and
  point `?dir=` at a model root; there is no implicit fixture under `models/`
  yet — add a small `*.js` implicit model plus a small mesh file to compare.

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
- Generalize `useViewerRuntime` to take a backend: its hardwired lights/scene
  groups/grid/background and the `renderer.render(scene, camera)` call become
  the **mesh backend**'s `attach`/`renderFrame`. It should import the shared
  helpers from `viewportCameraKit.js` directly instead of receiving ~30 of them
  as params from `CadViewer` (see the injection block in `CadViewer.js`).
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

### Phase 2 — unify perspective + close imperative-API gaps

- Converge the perspective snapshot format: implicit uses a separate
  `IMPLICIT_CAMERA_VERSION` payload (`perspectiveSnapshot`/`applyPerspectiveSnapshot`
  in `ImplicitCadViewer.js`); mesh uses `cadjs/lib/perspective` +
  `readScopedPerspectiveSnapshot`. Move to the mesh format so `getPerspective`/
  `setPerspective` payloads are interchangeable; version-migrate stored snapshots
  in `localStorage` so old implicit state degrades gracefully.
- Implement `resetZoom`/`zoomToFit`/`zoomToFitSelection` for the implicit viewer.
  `CadViewer` exposes all three; `ImplicitCadViewer` exposes only
  `captureScreenshot/getPerspective/setPerspective/focusViewPreset`, so the
  workbench's `viewerRef.current?.resetZoom()` / `?.zoomToFitSelection()` calls
  (`CadWorkspace.js`) silently no-op on implicit models today.

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
