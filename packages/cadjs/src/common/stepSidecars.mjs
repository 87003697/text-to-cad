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
// `<folder>/__cadcache__/models/<step-filename>/`. Recognized by structure (parent dir is
// `models`, grandparent is `__cadcache__`, basename is a STEP filename) — there is no
// `.step.glb` in the model tree anymore.
export function isInlineStepGlbArtifactPath(filePath) {
  const p = String(filePath || "");
  const name = path.basename(p).toLowerCase();
  if (!(name.endsWith(".step") || name.endsWith(".stp"))) {
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

// The render-artifact (component-GLB package) directory for a STEP source:
// `<folder>/__cadcache__/models/<step-filename>/` — a self-contained unit holding assembly.json
// plus its own `components/<hash>.glb` dir (no shared per-folder component store).
export function inlineStepGlbArtifactPathForSource(sourcePath) {
  return path.join(
    path.dirname(sourcePath),
    CACHE_DIRNAME,
    CACHE_MODELS_DIRNAME,
    path.basename(sourcePath),
  );
}

export function stepGlbArtifactPathForSource(sourcePath) {
  return inlineStepGlbArtifactPathForSource(sourcePath);
}

export function stepParameterPathForStepSource(sourcePath) {
  const stem = path.basename(sourcePath, path.extname(sourcePath));
  return path.join(path.dirname(sourcePath), `.${stem}.step.js`);
}
