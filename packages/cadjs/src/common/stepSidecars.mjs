import path from "node:path";

export const CACHE_DIRNAME = "__cadcache__";
const CACHE_MODELS_DIRNAME = "models";

export function isPerStepViewerDirectoryName(name) {
  const normalized = String(name || "").toLowerCase();
  return normalized.startsWith(".") && (normalized.endsWith(".step") || normalized.endsWith(".stp"));
}

// Whether a path lies inside a per-folder render cache (`__cadcache__/...`) — its
// descriptors and content-addressed component GLBs. These are served to the viewer but
// never recursed into during source discovery.
export function isInsideCadCache(filePath) {
  return String(filePath || "").split(path.sep).includes(CACHE_DIRNAME);
}

// A render artifact is the package DESCRIPTOR directory at
// `<folder>/__cadcache__/models/<entry-filename>/`. Recognized purely by STRUCTURE — parent
// dir is `models`, grandparent is `__cadcache__` — so the package may be named after ANY entry
// file (`<name>.step.py`, `<name>.step`, `<name>.stp`, or an arbitrary `<name>.blah.py`). The
// cache is unopinionated about the entry's extension; only the viewer decides which files are
// entrypoints.
export function isInlineStepGlbArtifactPath(filePath) {
  const p = String(filePath || "");
  if (!path.basename(p)) {
    return false;
  }
  return path.basename(path.dirname(p)) === CACHE_MODELS_DIRNAME
    && path.basename(path.dirname(path.dirname(p))) === CACHE_DIRNAME;
}

export function isInlineStepParameterPath(filePath) {
  const name = path.basename(String(filePath || "")).toLowerCase();
  return name.startsWith(".") && name.endsWith(".step.js");
}

export function isPathInsidePerStepViewerDirectory(filePath) {
  return String(filePath || "")
    .split(path.sep)
    .some((part) => isPerStepViewerDirectoryName(part));
}

// The render-artifact (component-GLB package) directory for a CAD ENTRY file:
// `<folder>/__cadcache__/models/<entry-filename>/` — a self-contained unit holding assembly.json
// plus its own `components/<hash>.glb` dir. Keyed by the entry filename VERBATIM (no extension
// stripping), so a generated `<name>.step.py` and an imported `<name>.step` get distinct packages.
export function inlineStepGlbArtifactPathForSource(entryPath) {
  return path.join(
    path.dirname(entryPath),
    CACHE_DIRNAME,
    CACHE_MODELS_DIRNAME,
    path.basename(entryPath),
  );
}

export function stepGlbArtifactPathForSource(entryPath) {
  return inlineStepGlbArtifactPathForSource(entryPath);
}

export function stepParameterPathForStepSource(sourcePath) {
  const stem = path.basename(sourcePath, path.extname(sourcePath));
  return path.join(path.dirname(sourcePath), `.${stem}.step.js`);
}
