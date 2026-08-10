# Render types: capabilities and the backend contract

Binding for viewer work that touches more than one file format. The rule this document
exists to enforce:

> Viewer code asks what a format **can do**, never what it **is**.

Every `renderFormat === RENDER_FORMAT.X` check is a place a new format must be
hand-added, and a place an improvement to one format fails to reach the others. That is
not theoretical. The Orbit button was gated off for implicit and for DXF independently and
had to be fixed twice; when it was finally enabled for DXF, the button still did nothing
because **four** separate format checks stood between it and preview mode (the toolbar
gate, the workspace handler bail, the pane's `previewMode={dxfMode ? false : ...}`, and an
effect that force-exited DXF from preview). Implicit meanwhile grew an entire parallel
export path to `/__cad/implicit-export`, an endpoint the server does not implement.

## The capability registry

`packages/cadjs/src/lib/renderCapabilities.js` — one frozen table, keyed by render
format. Pure data: no behaviour, no imports beyond the format enum.

| Capability | Meaning |
|---|---|
| `content` | Which loaded object is the viewport's content: `mesh`, `implicit`, `robot`. Resolved once into `selectedViewportContent`. |
| `sheetKind` | Which file-sheet section set mounts. |
| `label` | User-facing format name (status chips, sheet titles). |
| `sceneScale` | `cad` or `urdf`; picks the scene-scale profile. |
| `tools` | `select`, `pan`, `draw`, `orbit`, `screenshot`. Orbit and screenshot are true for everything — they act on the viewport, not the geometry. |
| `parts` | Per-part selection, hiding, isolate, assembly tree. |
| `topology` | Face/edge/vertex references. Implies `parts`. |
| `exploded`, `displayModes`, `clip` | STEP-tier display transforms. |
| `planView` | Offers the 2D/3D top-down lock. |
| `themeProjection` | Honours `themeSettings.projection`. |
| `params` | `sidecar` (`.step.js`), `module` (in-`.implicit.js`), or `null`. |
| `animations` | Has animation clips, so transport controls apply. |
| `posePicker` | Robot pose picking. |
| `artifactManaged` | Builds a package before it can render. **Must mirror `owns_entry` in `viewer/server_py/artifact.py`** — drift means an entry blocks on a build that never runs, or reports ready forever. |
| `exportFormats` | What `/__cad/export` can produce for it. |

### Rules

- Add a capability when the **second** format needs it, never speculatively.
- An unknown format resolves to the conservative default row (everything optional off).
  Deliberately *not* `normalizeRenderFormat`, which resolves unknowns to STEP and would
  hand an unrecognised entry STEP's full capability set.
- Capabilities decide **which** panels and tools mount. Format-specific *content* — STEP's
  tree, DXF's bends, an implicit's graphics tab — stays format-specific.

## The content signal

`selectedViewportContent` in `CadWorkspace` is the single answer to "is there anything on
screen?", derived from `content`. Toolbar gates, the CTA, preview mode, the zoom pill and
alert blocking all read it. Asking `!selectedMeshData` instead is what left an implicit's
screenshot and orbit buttons permanently disabled: a raymarched model never loads a mesh.

## The render-backend contract

`CadViewer` is the shell and owns the camera, `OrbitControls`, the themed stage, frame
insets, overlays, screenshots and the imperative viewer API. A **backend** owns geometry
only:

1. **Consume content** for its `content` kind (mesh data, implicit model, robot).
2. **Publish bounds** so the shared fit, zoom baseline and zoom-percent work. The mesh
   path does this via `applyRuntimeModelBounds` after composing; the implicit pass calls
   back with declared bounds, then refined bounds once its CPU SDF scan resolves.
3. **Optionally install loop-tuning hooks** on the runtime. All are inert unless set, so
   the mesh path is unaffected:
   - `renderOnDemandOnly` — do not hold the render loop open for a whole gesture.
   - `idleQualityDelayMs` — raise the idle-restore delay.
   - `onIdleQualityRestore` — restore quality before the pixel ratio, so the expensive
     frame and the drawing-buffer reallocation do not land on the same vsync.
   - `resolveExtraPixelRatioCap` — cap resolution below the shared caps.

A backend never reaches into the camera, controls or stage. If it needs something from
them, that is a shell feature and belongs in the shell where every format gets it.

### Adding a format

Declare a registry row, implement a backend, add a fixture to the sweep. Do not touch the
shell. If you find yourself adding a format check to `FloatingToolBar`, `CadRenderPane` or
`CadViewer`, the capability you need is missing from the table.

## Enforcement

`tests/python/global/test_viewer_format_capability_policy.py` counts identity checks in
non-test client code and **ratchets**: the number may only go down. It also asserts that
`FloatingToolBar` and `CadRenderPane` contain zero format checks, since those are the
components every format flows through. Lower the budgets in the same commit that removes
checks.

## Standing gate

`viewer/scripts/e2e-format-sweep.mjs` loads one fixture per format against a running
viewer and asserts each draws something with no page errors:

```bash
npm --prefix viewer run start -- --port 3245 --host 127.0.0.1   # from the models root
node viewer/scripts/e2e-format-sweep.mjs --dir <abs-models-root> [--all-implicits]
```

Run it for any change to shared viewer code. It uses `page.screenshot()` against a
Metal-backed context on purpose: a blank-but-error-free viewport is the signature failure
mode here (a shader that fails to compile, a gate that hides the geometry), sampling the
canvas with `drawImage` reports every format blank because the drawing buffer is not
preserved, and the software rasteriser hides real GPU failures. It has already earned its
keep — it caught a temporal-dead-zone crash that blanked all six formats and that the
build and unit tests both passed.

**Method warning: do not run large sweeps back to back.** Chaining full 47-model
`--all-implicits` runs (or launching several browsers in quick succession) exhausts GPU
contexts and reports large numbers of *false* blanks — a run that reported 33 blank models
reported zero on a clean run of the same build, twice. Let the previous run's browser fully
exit before starting another, and treat any mass-blank result as suspect until reproduced
from a cold start. Isolate a single suspect model rather than trusting one bulk run.

## Known non-uniformities

Recorded so they are not mistaken for bugs, and so the next person knows the cost:

- **Implicit cannot render orthographic.** Its shader marches from a single
  `rayOrigin = uCameraPosition`; an ortho camera needs per-pixel origins and a shared
  direction. Measured: forcing ortho makes the model vanish. STL/3MF/DXF render ortho
  correctly today with no code change, so `themeProjection` is honoured by the shell and
  only the implicit backend needs work (~15 lines: a `uOrthographic` uniform and a ray
  branch).
- **Implicit material support is partial.** The raymarcher has a fixed BRDF: theme
  `roughness`/`metalness`/`clearcoat`/`opacity`/`emissiveIntensity` are ignored and
  environment reflections are absent (the shader samples no textures); `environment.intensity`
  is folded into a flat ambient term. Colour shaping (tint/saturation/contrast/brightness)
  does apply, via the shared `shapeSourceColor`.
- **Implicit lighting is an approximation rig.** Exposure, hemisphere, ambient and key
  track the theme; `lighting.rim` is hardcoded and `lighting.fill` is unused (its "fill"
  is driven by `lighting.spot`). Fixing those two is pure wiring.
- **Select is inert for DXF and implicit.** Both keep the button for a uniform toolbar
  shape; neither has pickable topology.
