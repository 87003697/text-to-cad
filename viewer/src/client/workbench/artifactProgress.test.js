import assert from "node:assert/strict";
import test from "node:test";

import {
  artifactProgressRatio,
  formatArtifactProgress,
  normalizeArtifactProgress,
  renderProgressBar
} from "./artifactProgress.js";

function componentsPayload(overrides = {}) {
  return {
    phase: "components",
    label: "Meshing components",
    detail: "a1b2c3",
    done: 31,
    total: 50,
    determinate: true,
    ratio: 0.62,
    ratioFloor: 0.46,
    ratioCeiling: 0.96,
    phaseStartedAt: 1_000,
    phaseExpectedMs: 8_000,
    updatedAt: 5_000,
    ...overrides
  };
}

function indeterminatePayload(overrides = {}) {
  return componentsPayload({
    phase: "generate",
    label: "Building geometry",
    determinate: false,
    total: null,
    done: 0,
    ratio: 0,
    ratioFloor: 0,
    ratioCeiling: 0.4,
    phaseStartedAt: 1_000,
    phaseExpectedMs: 10_000,
    ...overrides
  });
}

test("normalizeArtifactProgress keeps a well-formed payload", () => {
  const progress = normalizeArtifactProgress(componentsPayload());
  assert.equal(progress.phase, "components");
  assert.equal(progress.label, "Meshing components");
  assert.equal(progress.done, 31);
  assert.equal(progress.total, 50);
  assert.equal(progress.determinate, true);
});

test("normalizeArtifactProgress returns null for anything unrenderable", () => {
  for (const raw of [null, undefined, "generating", 42, {}, { phase: "" }, { phase: "   " }]) {
    assert.equal(normalizeArtifactProgress(raw), null);
  }
});

test("normalizeArtifactProgress does not trust determinate without a total", () => {
  // The build never emits this, but the payload is a file another process wrote —
  // trusting the flag over the count would render "31/null".
  const progress = normalizeArtifactProgress(componentsPayload({ total: null }));
  assert.equal(progress.determinate, false);
});

test("normalizeArtifactProgress degrades non-numeric fields instead of producing NaN", () => {
  const progress = normalizeArtifactProgress(
    componentsPayload({ ratio: "hello", done: undefined, phaseStartedAt: null })
  );
  assert.equal(progress.ratio, 0);
  assert.equal(progress.done, 0);
  assert.equal(progress.phaseStartedAt, 0);
});

test("a determinate phase is reported exactly, never smoothed", () => {
  const progress = normalizeArtifactProgress(componentsPayload());
  // Same answer however much wall-clock time passes: the count is measured, and
  // inventing motion between two real counts would misreport the one measured stage.
  assert.equal(artifactProgressRatio(progress, 1_000), 0.62);
  assert.equal(artifactProgressRatio(progress, 900_000), 0.62);
});

test("an indeterminate phase advances through its band on the clock", () => {
  const progress = normalizeArtifactProgress(indeterminatePayload());
  assert.equal(artifactProgressRatio(progress, 1_000), 0);
  assert.ok(Math.abs(artifactProgressRatio(progress, 6_000) - 0.2) < 1e-9);
  // Capped at the band ceiling: overrunning the estimate must not spill into the next
  // phase's share of the bar.
  assert.ok(Math.abs(artifactProgressRatio(progress, 999_000) - 0.4) < 1e-9);
});

test("an indeterminate phase with no recorded expectation sits where the build reported", () => {
  const progress = normalizeArtifactProgress(
    indeterminatePayload({ ratio: 0.1, phaseExpectedMs: 0 })
  );
  assert.equal(artifactProgressRatio(progress, 500_000), 0.1);
});

test("interpolation never drops below the position the build reported", () => {
  const progress = normalizeArtifactProgress(indeterminatePayload({ ratio: 0.35 }));
  assert.equal(artifactProgressRatio(progress, 1_100), 0.35);
});

test("formatArtifactProgress surfaces the real counts for a determinate phase", () => {
  const frame = formatArtifactProgress(normalizeArtifactProgress(componentsPayload()));
  assert.equal(frame.percent, 62);
  assert.equal(frame.counts, "31/50");
  assert.equal(frame.label, "Meshing components");
});

test("formatArtifactProgress omits counts when the phase has none", () => {
  const frame = formatArtifactProgress(normalizeArtifactProgress(indeterminatePayload()));
  assert.equal(frame.counts, "");
});

test("formatArtifactProgress never reads 100%", () => {
  // The artifact is not ready until the build's own request resolves; a full bar
  // beside a still-spinning viewer reads as a hang.
  const frame = formatArtifactProgress(
    normalizeArtifactProgress(componentsPayload({ done: 50, ratio: 1 }))
  );
  assert.equal(frame.percent, 99);
});

test("formatArtifactProgress maps null progress to null, not a zeroed bar", () => {
  assert.equal(formatArtifactProgress(null), null);
});

test("renderProgressBar fills proportionally at a fixed width", () => {
  assert.equal(renderProgressBar(0, 10), "░░░░░░░░░░");
  assert.equal(renderProgressBar(0.5, 10), "█████░░░░░");
  assert.equal(renderProgressBar(1, 10), "██████████");
});

test("renderProgressBar clamps out-of-range input to the bar's width", () => {
  assert.equal(renderProgressBar(-5, 4), "░░░░");
  assert.equal(renderProgressBar(42, 4), "████");
  assert.equal(renderProgressBar(Number.NaN, 4), "░░░░");
});
