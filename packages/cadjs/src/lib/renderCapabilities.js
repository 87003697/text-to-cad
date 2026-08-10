import { RENDER_FORMAT, normalizeFormat } from "./fileFormats.js";

// WHAT EACH RENDER FORMAT CAN DO — one table, so viewer code asks "can this format
// do X?" instead of "is this format Y?".
//
// Every identity check spread through the client (`renderFormat === RENDER_FORMAT.DXF`,
// `isMeshRenderFormat(...)`, the `xxxMode` boolean piles) is a place a new format has to
// be hand-added and a place an improvement fails to reach the other formats. That is not
// hypothetical: the Orbit button was gated off for implicit and for DXF independently and
// had to be fixed twice, and implicit grew a whole parallel export route to an endpoint
// the server does not implement. Both were "this format was not on the list" bugs.
//
// So: a format is a ROW here plus a render backend (see viewer/docs/render-types.md).
// Shared behaviour lives in the viewer shell and reads this table; it is never re-derived
// from the format's identity.
//
// Rules for changing this file:
// - Add a capability when the SECOND format needs it, never speculatively.
// - Capabilities are DATA. No behaviour, no imports beyond the format enum.
// - Format-specific *content* (STEP's tree, DXF's bends, an implicit's graphics tab)
//   stays format-specific — the flags below decide WHICH panels mount, not what is in
//   them.

// Which loaded object is "the thing on screen" for this format. The viewer resolves it
// once into a single content signal; asking `!selectedMeshData` is what left implicit's
// screenshot and orbit buttons permanently disabled, since a raymarched model never
// loads a mesh.
export const VIEWPORT_CONTENT = Object.freeze({
  MESH: "mesh",
  IMPLICIT: "implicit",
  ROBOT: "robot"
});

// How parameters reach the viewer: from a sibling `.step.js` sidecar module, or declared
// inside the `.implicit.js` model itself. Different stores, one consumer surface.
export const PARAMETER_SOURCE = Object.freeze({
  SIDECAR: "sidecar",
  MODULE: "module"
});

const MESH_CAPABILITIES = Object.freeze({
  content: VIEWPORT_CONTENT.MESH,
  sheetKind: "mesh",
  label: "STL",
  // A plain mesh is one body with no topology, no parts and no per-file settings: the
  // minimal row, and the useful floor for what "shared" has to mean.
  tools: Object.freeze({ select: false, pan: false, draw: false, orbit: true, screenshot: true }),
  parts: false,
  topology: false,
  exploded: false,
  displayModes: false,
  clip: false,
  planView: false,
  themeProjection: true,
  params: null,
  animations: false,
  artifactManaged: false,
  exportFormats: Object.freeze([])
});

const ROBOT_CAPABILITIES = Object.freeze({
  content: VIEWPORT_CONTENT.ROBOT,
  sheetKind: RENDER_FORMAT.URDF,
  label: "URDF",
  sceneScale: "urdf",
  tools: Object.freeze({ select: false, pan: false, draw: false, orbit: true, screenshot: true }),
  parts: false,
  topology: false,
  exploded: false,
  displayModes: false,
  clip: false,
  planView: false,
  themeProjection: true,
  params: null,
  animations: false,
  posePicker: true,
  artifactManaged: false,
  exportFormats: Object.freeze([])
});

const DEFAULT_CAPABILITIES = Object.freeze({
  content: VIEWPORT_CONTENT.MESH,
  sheetKind: "",
  label: "",
  sceneScale: "cad",
  tools: Object.freeze({ select: false, pan: false, draw: false, orbit: true, screenshot: true }),
  parts: false,
  topology: false,
  exploded: false,
  displayModes: false,
  clip: false,
  planView: false,
  // Projection is a THEME trait. Every format honours it; only the implicit raymarcher
  // needed shader work to accept a parallel-ray camera.
  themeProjection: true,
  params: null,
  animations: false,
  posePicker: false,
  // Formats whose viewport content comes from a GENERATED package, so the viewer checks
  // freshness and may trigger a build. MUST mirror `owns_entry` in
  // viewer/server_py/artifact.py — a format listed here and not there (or the reverse)
  // either blocks forever or reports ready forever.
  artifactManaged: false,
  exportFormats: Object.freeze([])
});

export const RENDER_CAPABILITIES = Object.freeze({
  [RENDER_FORMAT.STEP]: Object.freeze({
    ...DEFAULT_CAPABILITIES,
    sheetKind: RENDER_FORMAT.STEP,
    label: "STEP",
    tools: Object.freeze({ select: true, pan: true, draw: true, orbit: true, screenshot: true }),
    parts: true,
    topology: true,
    exploded: true,
    displayModes: true,
    clip: true,
    params: PARAMETER_SOURCE.SIDECAR,
    animations: true,
    artifactManaged: true,
    exportFormats: Object.freeze(["step", "3mf", "stl", "glb"])
  }),
  [RENDER_FORMAT.STL]: Object.freeze({ ...DEFAULT_CAPABILITIES, ...MESH_CAPABILITIES, label: "STL" }),
  [RENDER_FORMAT.THREE_MF]: Object.freeze({ ...DEFAULT_CAPABILITIES, ...MESH_CAPABILITIES, label: "3MF" }),
  [RENDER_FORMAT.GLB]: Object.freeze({ ...DEFAULT_CAPABILITIES, ...MESH_CAPABILITIES, label: "GLB" }),
  [RENDER_FORMAT.DXF]: Object.freeze({
    ...DEFAULT_CAPABILITIES,
    sheetKind: RENDER_FORMAT.DXF,
    label: "DXF",
    // Select is inert (a drawing has no pickable topology) but stays visible so the
    // toolbar keeps one shape; pan and draw act on the viewport, not the geometry.
    tools: Object.freeze({ select: true, pan: true, draw: true, orbit: true, screenshot: true }),
    planView: true,
    artifactManaged: true,
    exportFormats: Object.freeze(["dxf"])
  }),
  [RENDER_FORMAT.IMPLICIT]: Object.freeze({
    ...DEFAULT_CAPABILITIES,
    content: VIEWPORT_CONTENT.IMPLICIT,
    sheetKind: RENDER_FORMAT.IMPLICIT,
    label: "Implicit",
    tools: Object.freeze({ select: true, pan: true, draw: true, orbit: true, screenshot: true }),
    params: PARAMETER_SOURCE.MODULE,
    animations: true,
    // Rendered live from its own GLSL: there is no baked artifact to be stale, so an
    // implicit never blocks on a build and never shows a generating state.
    artifactManaged: false,
    exportFormats: Object.freeze(["stl", "glb", "3mf"])
  }),
  [RENDER_FORMAT.URDF]: Object.freeze({ ...DEFAULT_CAPABILITIES, ...ROBOT_CAPABILITIES, label: "URDF" }),
  [RENDER_FORMAT.SRDF]: Object.freeze({
    ...DEFAULT_CAPABILITIES,
    ...ROBOT_CAPABILITIES,
    sheetKind: RENDER_FORMAT.SRDF,
    label: "SRDF"
  }),
  [RENDER_FORMAT.SDF]: Object.freeze({
    ...DEFAULT_CAPABILITIES,
    ...ROBOT_CAPABILITIES,
    sheetKind: RENDER_FORMAT.SDF,
    label: "SDF"
  })
});

// Extension aliases that name the same render format.
const FORMAT_ALIASES = Object.freeze({
  stp: RENDER_FORMAT.STEP,
  gltf: RENDER_FORMAT.GLB
});

// Capabilities for a render format. An unrecognised format gets the conservative default
// row: it renders, and every optional capability is off.
//
// Deliberately NOT normalizeRenderFormat(), which resolves anything unknown to STEP. That
// is the right default when picking a loader, and exactly wrong here — it would hand an
// unrecognised entry STEP's full capability set (parts, topology, clip, artifact-managed
// gating) and fail open on every one of them.
export function renderCapabilities(renderFormat) {
  const normalized = normalizeFormat(renderFormat);
  const resolved = FORMAT_ALIASES[normalized] || normalized;
  return RENDER_CAPABILITIES[resolved] || DEFAULT_CAPABILITIES;
}

// `renderCapabilities(f).tools[tool]`, with the lookup and the missing-tool case handled.
export function supportsTool(renderFormat, tool) {
  return renderCapabilities(renderFormat).tools?.[tool] === true;
}

// Reads one boolean capability. Keeps call sites free of optional chaining and makes the
// policy ratchet's allowlist easy to describe: capability reads look like this.
export function hasCapability(renderFormat, capability) {
  return renderCapabilities(renderFormat)[capability] === true;
}

export function viewportContentKind(renderFormat) {
  return renderCapabilities(renderFormat).content;
}

export function renderFormatLabel(renderFormat) {
  return renderCapabilities(renderFormat).label;
}

export function exportFormatsForRenderFormat(renderFormat) {
  return renderCapabilities(renderFormat).exportFormats;
}

export function isArtifactManagedFormat(renderFormat) {
  return renderCapabilities(renderFormat).artifactManaged === true;
}
