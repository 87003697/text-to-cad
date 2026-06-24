import test from "node:test";
import assert from "node:assert/strict";

import { ARTIFACT_STATE } from "./renderArtifact.mjs";
import { createStepArtifactProvider } from "./stepArtifactProvider.mjs";

function makeProvider(overrides = {}) {
  return createStepArtifactProvider({
    ownsEntry: (entry) => entry?.kind === "assembly" || entry?.kind === "part",
    readStatus: () => ({ ok: true, artifact: null }),
    build: async () => ({ ok: true }),
    artifactRef: (entry) => String(entry?.url || ""),
    ...overrides,
  });
}

test("owns delegates to ownsEntry", () => {
  const p = makeProvider();
  assert.equal(p.owns({ kind: "assembly" }), true);
  assert.equal(p.owns({ kind: "stl" }), false);
  assert.equal(p.kind, "step");
});

test("status: a fresh package (no artifact error) is ready", () => {
  const p = makeProvider({ readStatus: () => ({ ok: false, artifact: null }) });
  assert.equal(p.status({ entry: { url: "/pkg" } }).state, ARTIFACT_STATE.READY);
  assert.equal(p.status({ entry: { url: "/pkg" } }).ref, "/pkg");
});

test("status: a stale/missing package is needs-build (a trigger, not an issue)", () => {
  for (const error of ["stale_step_artifact", "missing_glb", "missing_source_path"]) {
    const p = makeProvider({ readStatus: () => ({ ok: false, artifact: { ok: false, error } }) });
    assert.equal(p.status({ entry: {} }).state, ARTIFACT_STATE.NEEDS_BUILD, error);
  }
});

test("status: a non-buildable artifact error is a fatal error with its message", () => {
  const p = makeProvider({ readStatus: () => ({ ok: false, artifact: { ok: false, error: "totally_broken", message: "kaboom." } }) });
  const r = p.status({ entry: {} });
  assert.equal(r.state, ARTIFACT_STATE.ERROR);
  assert.equal(r.error, "kaboom.");
});

test("status: an unresolvable source is a fatal error, not a crash", () => {
  const p = makeProvider({ readStatus: () => { throw new Error("outside root"); } });
  const r = p.status({ entry: {} });
  assert.equal(r.state, ARTIFACT_STATE.ERROR);
  assert.equal(r.error, "outside root");
});

test("build: success -> ready, failure -> error", async () => {
  const ok = makeProvider({ build: async () => ({ ok: true }) });
  assert.equal((await ok.build({ entry: { url: "/pkg" } })).state, ARTIFACT_STATE.READY);

  const fail = makeProvider({ build: async () => ({ ok: false, error: "generator threw" }) });
  const r = await fail.build({ entry: {} });
  assert.equal(r.state, ARTIFACT_STATE.ERROR);
  assert.equal(r.error, "generator threw");
});

test("build: passes fileRef/force/root/catalog through to the builder", async () => {
  let seen = null;
  const p = makeProvider({ build: async (args) => { seen = args; return { ok: true }; } });
  await p.build({ fileRef: "a.step", force: true, resolvedRoot: { dir: "d" }, catalog: { x: 1 }, entry: {} });
  assert.equal(seen.fileRef, "a.step");
  assert.equal(seen.force, true);
  assert.deepEqual(seen.resolvedRoot, { dir: "d" });
});
