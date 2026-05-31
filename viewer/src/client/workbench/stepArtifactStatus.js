import { RENDER_FORMAT } from "cadjs/lib/fileFormats.js";

export const STEP_ARTIFACT_GENERATION_FAILURE_DISPLAY_THRESHOLD = 3;

const BUILDABLE_STEP_ARTIFACT_ERROR_CODES = new Set([
  "missing_glb",
  "missing_step_topology",
  "missing_edge_topology",
  "missing_surface_edge_attributes",
  "missing_selector_topology",
  "missing_source_path",
  "missing_step_hash",
  "stale_step_artifact",
  "unsupported_step_topology"
]);

export function stepArtifactGenerationFailureCount(state) {
  const count = Number(state?.failureCount || 0);
  return Number.isFinite(count) && count > 0 ? Math.trunc(count) : 0;
}

export function stepArtifactIsStale(entry, sourceFormat) {
  return (
    sourceFormat === RENDER_FORMAT.STEP &&
    entry?.artifact?.ok === false &&
    (
      entry.artifact.stale === true ||
      String(entry.artifact.error || "") === "stale_step_artifact"
    )
  );
}

export function stepArtifactCanGenerate(entry, sourceFormat, { generationAvailable = true } = {}) {
  if (!generationAvailable || sourceFormat !== RENDER_FORMAT.STEP) {
    return false;
  }
  if (entry?.artifact?.ok) {
    return false;
  }
  return BUILDABLE_STEP_ARTIFACT_ERROR_CODES.has(String(entry?.artifact?.error || ""));
}

export function stepArtifactNeedsWarning(entry, sourceFormat, options = {}) {
  return (
    sourceFormat === RENDER_FORMAT.STEP &&
    entry?.artifact?.ok === false &&
    !stepArtifactCanGenerate(entry, sourceFormat, options)
  );
}
