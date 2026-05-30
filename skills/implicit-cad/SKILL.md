---
name: implicit-cad
description: Create, edit, render, and snapshot browser-native implicit CAD `.implicit.js` and `.implicit.mjs` files using GLSL signed-distance fields, shader primitives, smooth booleans, TPMS fields, and direct CAD Viewer raymarch rendering.
---

# Implicit CAD

Use this skill for implicit CAD models that should run directly in CAD Viewer as browser JS modules. The primary artifact is a `.implicit.js` or `.implicit.mjs` file under `models/`.

## File Format

An implicit CAD file is an ES module exporting an `implicit-cad/v1` object:

```js
export default {
  schema: "implicit-cad/v1",
  name: "rounded capsule block",
  glsl: `
float sdf(vec3 p) {
  float sphere = implicitCadSphere(p, vec3(0.0), 22.0);
  float block = implicitCadBoxCentered(p, vec3(34.0, 18.0, 18.0), vec3(0.0));
  return implicitCadUnionRound(sphere, block, 3.0);
}

vec3 color(vec3 p, vec3 normal) {
  return mix(vec3(0.20, 0.55, 0.95), vec3(0.95, 0.45, 0.20), smoothstep(-15.0, 20.0, p.z));
}
`
};
```

Models may also declare viewer params and animations. Parameter definitions use the same control schema as CAD Viewer STEP modules: `number`, `boolean`, `enum`/`select`, `color`, `string`, and `button`. Number, boolean, color, and button params automatically become GLSL uniforms with the same name; do not add a separate `uniforms` object. `bounds` is optional and is estimated from the SDF when omitted; add explicit bounds only when the auto estimate is too broad, too slow, or misses an unusual field. `bounds` and `render` may be JavaScript functions that receive `{ ...params, params, animation, animationState, elapsedSec, progress, t }`.

```js
export default {
  schema: "implicit-cad/v1",
  name: "breathing orb",
  params: {
    radius: { type: "number", label: "Radius", min: 12, max: 34, default: 22, unit: "mm" }
  },
  animations: {
    breathe: {
      label: "Breathe",
      duration: 3,
      update({ progress, set }) {
        set("radius", 18 + Math.sin(progress * Math.PI) * 10);
      }
    }
  },
  render: { steps: 224, epsilon: 0.004 },
  glsl: `
float sdf(vec3 p) {
  return length(p) - radius;
}

vec3 color(vec3 p, vec3 normal) {
  return mix(vec3(0.10, 0.58, 0.95), vec3(1.0, 0.34, 0.12), smoothstep(-18.0, 18.0, p.z));
}
`
};
```

Keep generated model files and helper copies in `models/`. If a model imports the helper library, copy `scripts/lib/implicit-cad.mjs` next to the model unless an existing sibling copy is already present.

## Authoring Workflow

1. Write a natural-language modeling brief with dimensions, coordinate assumptions, procedural color intent, and visual checks.
2. Create or edit a `.implicit.js`/`.implicit.mjs` module in `models/`.
3. Use `scripts/lib/implicit-cad.mjs` helpers for primitives and field composition when useful:
   - primitives: `sphere`, `circle`, `boxCentered`, `plane`, `lineSegment`, `torus`, `axis`, `cylinder`, `cylinderCapped`, `capsule`, `cone`, `coneCapped`, `coneCapsule`
   - booleans/blends: `unionSharp`, `intersectSharp`, `unionRound`, `intersectRound`, `unionChamfer`, `intersectChamfer`, `unionExp`, `intersectExp`, `unionLpNorm`, `intersectLpNorm`, `unionRvachev`, `intersectRvachev`, `difference`
   - modifiers/lattices: `shell`, `rotateAxis`, `repeatCentered`, `remapCylindrical`, `cubicGrid`, `squareHoneycomb`, `squareHoneycombReinforced`, `squareDiagonalHoneycomb`, `octetHoneycomb`, `hexagonalHoneycomb`, `triangularHoneycomb`
   - TPMS fields: `tpmsGyroid`, `tpmsSchwarz`, `tpmsDiamond`, `tpmsLidinoid`, `tpmsNeovius`, `tpmsSplitP`, `tpmsIwp`
   - shader wrappers: `distanceFunction` emits `float sdf(vec3 p)`, `colorFunction` emits `vec3 color(vec3 p, vec3 normal)`
4. Add optional `params` and `animations` for dimensions, toggles, palettes, mode switches, and animated exploration. Use param names directly in GLSL; the runtime declares matching uniforms.
5. Add optional procedural color with `vec3 color(vec3 p, vec3 normal)` when the model benefits from local material variation. Keep color values in 0..1 RGB.
6. Rely on automatic SDF bounds first. Add explicit bounds when an animated, periodic, translated, or very thin model needs tighter or more reliable framing/export sampling.
7. Run `python scripts/snapshot --input <model.implicit.js> --output <png>` for visual verification after visible changes.
8. Run `python scripts/export --input <model.implicit.js> --format glb` when a mesh artifact is needed for downstream viewers, slicers, or file handoff.
9. Hand the explicit `.implicit.js`/`.implicit.mjs` file to `$cad-viewer` for live review links when available.

## Snapshot Tool

From this skill directory:

```bash
python scripts/snapshot --input /path/to/model.implicit.js --output /tmp/model.png
python scripts/snapshot --input /path/to/model.implicit.js --output /tmp/orbit.gif --mode orbit
python scripts/snapshot --job render-job.json --json
```

Shortcut flags:

- `--input`: implicit CAD module path.
- `--output` / `-o`: PNG or GIF output path. The tool appends a UTC timestamp before the extension.
- `--mode`: `view`, `orbit`, or `animate`. GIF shortcut outputs default to `orbit`.
- `--appearance`: saved theme name such as `workbench`, inline JSON appearance settings, or a JSON file path. Defaults to the light workbench appearance.
- `--camera`: `iso`, `front`, `right`, `top`, `azimuth:elevation`, or a JSON camera object with `preset`, `position`, `target`, `up`, `direction`, and `zoom`.
- `--size-profile`: shared snapshot size profile such as `simple`, `diagnostic`, `presentation`, `assembly`, or `orbit`.
- `--width`, `--height`: explicit output dimensions.
- `--params`: implicit parameter JSON object for shortcut renders.
- `--graphics`: implicit graphics JSON object or JSON file path. Supported fields are `resolutionScale`, `interactionResolutionScale`, `detail`, `normalSmoothing`, `modelColors`, `shadows`, `ambientOcclusion`, and `rimLight`.
- `--job`: JSON job, `-` for stdin. Jobs use `input`, `mode`, `outputs`, `camera`, `appearance`, `graphics`, optional `implicitParameters`, optional `implicitAnimation`/`orbit`, and optional `render.transparent`.

The snapshot tool intentionally omits STEP-specific features such as topology, refs, section/list modes, and GLB sidecars.

## Export Tool

From this skill directory:

```bash
python scripts/export --input /path/to/model.implicit.js --format glb
python scripts/export --input /path/to/model.implicit.js --output /tmp/model.stl --resolution 48
python scripts/export --input /path/to/model.implicit.js --format 3mf --params '{"radius": 24}' --json
```

Supported export formats are `glb`, `stl`, and `3mf`. The exporter samples the implicit SDF inside the declared bounds and extracts a triangle mesh. If `--output` is omitted, the mesh is written next to the source file using the same stem, such as `part.glb` for `part.implicit.js`.

Shortcut flags:

- `--input`: implicit CAD module path.
- `--output` / `-o`: mesh output path.
- `--format`: `glb`, `stl`, or `3mf`; inferred from `--output` when omitted.
- `--resolution`: longest-axis sampling resolution; higher values produce denser meshes and take longer.
- `--max-cells`: cap sampled grid cells for safety.
- `--params`: implicit parameter JSON object.
- `--animation`: implicit animation-state JSON object.
- `--json`: print machine-readable export details.

## Validation

For code changes to this skill or renderer, run focused checks:

```bash
npm --prefix packages/implicitjs test
npm --prefix packages/cadjs test -- src/lib/fileFormats.test.js src/lib/cadDirectoryScanner.test.mjs
npm --prefix viewer run test
scripts/build/build-viewer.sh --check
scripts/build/build-implicit-cad-skill.sh --check
scripts/build/build-cad-viewer-skill.sh --check
scripts/build/build-plugin.sh --check
```

When changing generated copies, run the corresponding build script without `--check`, then rerun with `--check`.
