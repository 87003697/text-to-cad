import test from "node:test";
import assert from "node:assert/strict";

import {
  ARTIFACT_STATE,
  classifyArtifactFreshness,
  createRenderArtifactPipeline,
} from "./renderArtifact.mjs";

const BUILDABLE = new Set(["missing_glb", "stale_step_artifact"]);

test("classifyArtifactFreshness: ok artifact is ready", () => {
  assert.deepEqual(classifyArtifactFreshness({ ok: true }, BUILDABLE), { state: ARTIFACT_STATE.READY });
  // no artifact (undefined ok) is treated as ready (direct/healthy)
  assert.deepEqual(classifyArtifactFreshness({}, BUILDABLE), { state: ARTIFACT_STATE.READY });
});

test("classifyArtifactFreshness: a buildable error code is needs-build, not an issue", () => {
  assert.equal(classifyArtifactFreshness({ ok: false, error: "stale_step_artifact" }, BUILDABLE).state, ARTIFACT_STATE.NEEDS_BUILD);
  assert.equal(classifyArtifactFreshness({ ok: false, error: "missing_glb" }, BUILDABLE).state, ARTIFACT_STATE.NEEDS_BUILD);
});

test("classifyArtifactFreshness: a non-buildable error is a fatal error", () => {
  const r = classifyArtifactFreshness({ ok: false, error: "exploded" }, BUILDABLE);
  assert.equal(r.state, ARTIFACT_STATE.ERROR);
  assert.equal(r.error, "exploded");
});

test("pipeline: an entry no provider owns renders directly (always ready)", async () => {
  const pipeline = createRenderArtifactPipeline({ providers: [], directRef: (e) => `/url/${e.file}` });
  const entry = { file: "a.stl", kind: "stl" };
  assert.deepEqual(pipeline.artifactStatus({ entry }), { state: ARTIFACT_STATE.READY, ref: "/url/a.stl" });
  assert.deepEqual(await pipeline.resolveArtifact({ entry }), { ok: true, state: ARTIFACT_STATE.READY, ref: "/url/a.stl" });
  assert.equal(pipeline.providerFor(entry), null);
});

test("pipeline: dispatches to the owning provider", async () => {
  const calls = [];
  const provider = {
    kind: "step",
    owns: (e) => e.kind === "assembly",
    status: (args) => { calls.push(["status", args.entry.file]); return { state: ARTIFACT_STATE.NEEDS_BUILD }; },
    build: async (args) => { calls.push(["build", args.entry.file]); return { ok: true, state: ARTIFACT_STATE.READY }; },
  };
  const pipeline = createRenderArtifactPipeline({ providers: [provider], directRef: () => "" });
  const entry = { file: "x.step", kind: "assembly" };
  assert.equal(pipeline.providerFor(entry), provider);
  assert.equal(pipeline.artifactStatus({ entry }).state, ARTIFACT_STATE.NEEDS_BUILD);
  assert.equal((await pipeline.resolveArtifact({ entry })).state, ARTIFACT_STATE.READY);
  assert.deepEqual(calls, [["status", "x.step"], ["build", "x.step"]]);
});

test("pipeline: a provider that throws in owns() is skipped, not fatal", () => {
  const bad = { owns: () => { throw new Error("boom"); }, status: () => ({}), build: async () => ({}) };
  const pipeline = createRenderArtifactPipeline({ providers: [bad], directRef: () => "/fallback" });
  assert.deepEqual(pipeline.artifactStatus({ entry: { file: "z" } }), { state: ARTIFACT_STATE.READY, ref: "/fallback" });
});
