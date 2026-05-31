# implicitjs

`implicitjs` is the standalone JavaScript runtime for browser-native implicit
CAD models. It owns the `.implicit.js` model schema, GLSL shader assembly,
Three.js raymarch rendering, headless snapshot entrypoint, CPU SDF sampling,
mesh quality checks, and STL/3MF/GLB exporters.

The package is UI-framework agnostic. CAD Viewer owns React state and chrome;
`implicitjs` owns reusable implicit CAD behavior that can run in the viewer,
snapshot tool, export tool, tests, or a future standalone package/repo.

## Install

In this workbench, consumers link the package directly:

```json
{
  "dependencies": {
    "implicitjs": "file:../packages/implicitjs"
  }
}
```

The package exports source files so local consumers can install it directly and
pick up edits without generated bundles.

## Layout

- `src/index.js`: public package entrypoint.
- `src/lib/implicitCad/`: model normalization, loader, graphics settings,
  shader renderer, CPU evaluator, mesh sampler, mesh quality checks, and
  browser/Node-safe model exporters.
- `src/common/`: reusable camera, parameters, theme, render options, and
  headless snapshot entrypoint code.
- `src/lib/viewer/`: small viewer-adjacent helpers needed by the implicit
  renderer without depending on CAD Viewer or `cadjs`.
- `scripts/`: test, export, and export-verification CLIs.

Tests live beside the modules they cover as `*.test.js`.

## Commands

From this package directory:

```bash
npm test
npm run verify:exports -- --input ../../models/implicit-cad/rounded-orb.implicit.js
```

From the workbench repository root:

```bash
npm --prefix packages/implicitjs test
node packages/implicitjs/scripts/export-implicit-cad.mjs \
  --input models/implicit-cad/rounded-orb.implicit.js \
  --output /tmp/rounded-orb.glb

node packages/implicitjs/scripts/export-implicit-cad.mjs \
  --input models/implicit-cad/planetary-gear.implicit.js \
  --animated \
  --animation '{"activeId":"meshCycle"}' \
  --frames 24 \
  --output /tmp/planetary-gear.animated.glb
```

Browser consumers can import `exportImplicitModel` for STL/3MF/GLB buffers and
`exportImplicitAnimatedGlb` for morph-target GLB animation buffers from the
browser entrypoint.

## Boundaries

Keep implicit CAD runtime behavior here when it is reusable across:

- interactive browser raymarch rendering,
- headless snapshots and GIFs,
- CPU SDF sampling,
- mesh exports,
- implicit model schema normalization,
- implicit parameter and animation runtime behavior.

Keep CAD catalog scanning, file-sheet UI, URL/session state, sidebar controls,
and other product workflows in `viewer/` or `cadjs`.
