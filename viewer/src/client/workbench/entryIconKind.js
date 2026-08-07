import {
  isRobotRenderFormat,
  normalizeFormat,
  RENDER_FORMAT
} from "cadjs/lib/fileFormats.js";

export const ENTRY_ICON_KIND = Object.freeze({
  LOADING: "loading",
  DXF: "dxf",
  IMPLICIT: "implicit",
  ROBOT: "robot",
  STEP: "step",
  STL_MESH: "stl-mesh",
  THREE_MF_MESH: "3mf-mesh",
  GLB_MESH: "glb-mesh"
});

// Whether an entry's geometry is produced by running a generator script — a
// `.step.py` or `.dxf.py`, or a catalog entry marked sourceKind "python".
//
// This does NOT change which icon the entry gets: a generated model shows the
// same icon as the imported file it stands in for, so a `.step.py` assembly and
// an imported `.step` assembly read alike in the file list. Being code-derived
// is carried by the small badge in the icon's bottom-right corner instead.
//
// An `.implicit.js` is not included: it is not generated FROM code, it IS the
// code, so it keeps its own icon and takes no badge.
export function isCodeDerivedEntry(entry) {
  if (String(entry?.sourceKind || "").trim().toLowerCase() === "python") {
    return true;
  }
  const sourcePath = String(
    entry?.source?.sourcePath || entry?.source?.file || entry?.file || ""
  ).toLowerCase();
  return sourcePath.endsWith(".step.py") || sourcePath.endsWith(".dxf.py");
}

export function entryIconKind(entry, {
  sourceFormat = "",
  status = {}
} = {}) {
  const normalizedSourceFormat = normalizeFormat(sourceFormat || entry?.kind);
  const normalizedKind = normalizeFormat(entry?.kind);
  const safeStatus = status || {};

  if (safeStatus.artifactGenerating || safeStatus.loading) {
    return ENTRY_ICON_KIND.LOADING;
  }
  if (normalizedSourceFormat === RENDER_FORMAT.DXF || normalizedKind === RENDER_FORMAT.DXF) {
    return ENTRY_ICON_KIND.DXF;
  }
  if (normalizedSourceFormat === RENDER_FORMAT.IMPLICIT || normalizedKind === RENDER_FORMAT.IMPLICIT) {
    return ENTRY_ICON_KIND.IMPLICIT;
  }
  if (isRobotRenderFormat(normalizedSourceFormat) || normalizedKind === "srdf") {
    return ENTRY_ICON_KIND.ROBOT;
  }
  if (normalizedSourceFormat === RENDER_FORMAT.STL || normalizedKind === RENDER_FORMAT.STL) {
    return ENTRY_ICON_KIND.STL_MESH;
  }
  if (normalizedSourceFormat === RENDER_FORMAT.THREE_MF || normalizedKind === RENDER_FORMAT.THREE_MF) {
    return ENTRY_ICON_KIND.THREE_MF_MESH;
  }
  if (normalizedSourceFormat === RENDER_FORMAT.GLB || normalizedSourceFormat === "gltf" || normalizedKind === RENDER_FORMAT.GLB || normalizedKind === "gltf") {
    return ENTRY_ICON_KIND.GLB_MESH;
  }
  // STEP family: one icon for the format. Part vs assembly is structure, not
  // file type, and the tree already shows it — splitting the icon only made two
  // glyphs the reader has to learn for the same kind of file. Generated and
  // imported share it too; the code badge carries that difference.
  return ENTRY_ICON_KIND.STEP;
}
