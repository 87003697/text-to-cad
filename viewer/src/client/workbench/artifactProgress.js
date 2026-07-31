// Reading the build-progress payload the artifact-status route attaches while a model
// is generating (written by cadgen's _internal/progress.py, served by
// server_py/artifact.py's read_generation_progress).
//
// The build reports at WORK BOUNDARIES, never on a timer: OCP meshing holds Python's
// GIL inside C for long stretches, so a heartbeat thread in the build process would
// starve during exactly the work it was meant to report. Interpolation is therefore the
// READER's job, and it splits cleanly in two:
//
// * a determinate phase (meshing components) is shown exactly as reported -- a real
//   done/total, never smoothed, because inventing motion between two measured counts
//   would misreport the one stage we actually measure;
// * an indeterminate phase (a generator running, a STEP parsing) is advanced against
//   the wall clock inside its band, using the duration this model's PREVIOUS build
//   recorded for that phase. Absent that, it simply sits at the band's floor.
//
// Progress never reaches 100% here. The artifact is not ready until the build's own
// request resolves, and a bar that sits full while the viewer still shows a spinner
// reads as a hang.

export const ARTIFACT_PROGRESS_POLL_MS = 400;
// The first poll runs sooner than the rest. The overlay grows by two rows the moment
// progress first arrives, and that is the one layout shift the stacked design cannot
// avoid — so it should happen while the loading state is still appearing, not a
// noticeable beat later. The build reports its opening phase immediately, so there is
// something to read this early.
export const ARTIFACT_PROGRESS_FIRST_POLL_MS = 120;
const MAX_DISPLAY_PERCENT = 99;

function clamp01(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return 0;
  }
  return Math.min(1, Math.max(0, numeric));
}

function finiteNumber(value, fallback = 0) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

// Normalizes the server payload into the shape the UI reads, or null when there is
// nothing renderable. Defensive by intent: this is a file on disk written by another
// process, so a partial or unexpected payload must degrade to "no progress", never to a
// broken overlay.
export function normalizeArtifactProgress(raw) {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const phase = String(raw.phase || "").trim();
  if (!phase) {
    return null;
  }
  const total = raw.total === null || raw.total === undefined ? null : finiteNumber(raw.total, 0);
  const determinate = Boolean(raw.determinate) && Number(total) > 0;
  return {
    phase,
    label: String(raw.label || "").trim(),
    detail: String(raw.detail || "").trim(),
    done: Math.max(0, Math.round(finiteNumber(raw.done, 0))),
    total: total === null ? null : Math.max(0, Math.round(total)),
    determinate,
    ratio: clamp01(raw.ratio),
    ratioFloor: clamp01(raw.ratioFloor),
    ratioCeiling: clamp01(raw.ratioCeiling),
    phaseStartedAt: finiteNumber(raw.phaseStartedAt, 0),
    phaseExpectedMs: finiteNumber(raw.phaseExpectedMs, 0),
    updatedAt: finiteNumber(raw.updatedAt, 0)
  };
}

export function artifactProgressRatio(progress, nowMs = Date.now()) {
  if (!progress) {
    return 0;
  }
  if (progress.determinate) {
    return clamp01(progress.ratio);
  }
  if (!progress.phaseExpectedMs || !progress.phaseStartedAt) {
    return clamp01(progress.ratio);
  }
  const elapsed = Math.max(0, nowMs - progress.phaseStartedAt);
  const within = Math.min(1, elapsed / progress.phaseExpectedMs);
  const interpolated = progress.ratioFloor + (progress.ratioCeiling - progress.ratioFloor) * within;
  // Never below what the build itself reported: the recorded expectation can be wrong,
  // but a measured position is not.
  return clamp01(Math.max(progress.ratio, interpolated));
}

// The display model for one frame: a ratio, a capped percent, the phase label, and the
// real counts when (and only when) the phase has them.
export function formatArtifactProgress(progress, nowMs = Date.now()) {
  if (!progress) {
    return null;
  }
  const ratio = artifactProgressRatio(progress, nowMs);
  return {
    ratio,
    percent: Math.min(MAX_DISPLAY_PERCENT, Math.round(ratio * 100)),
    label: progress.label,
    counts: progress.determinate && progress.total ? `${progress.done}/${progress.total}` : "",
    determinate: progress.determinate
  };
}

// A fixed-width monospace bar, matching the loading overlay's ASCII treatment. Same
// glyphs the CLI uses, so the two surfaces read as one feature.
export function renderProgressBar(ratio, width = 18) {
  const cells = Math.max(1, Math.round(width));
  const filled = Math.round(clamp01(ratio) * cells);
  return `${"█".repeat(filled)}${"░".repeat(cells - filled)}`;
}
