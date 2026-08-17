import assert from "node:assert/strict";
import test from "node:test";
import { parseRequest } from "../contract.mjs";

const triangle = {
  vertices: [[-0.4, -0.3, 0], [0.4, -0.3, 0], [0, 0.4, 0]],
  faces: [[0, 1, 2]],
};
const residual = {
  reference: triangle,
  candidate: triangle,
  variant: "step",
  exteriorDirections: [],
  options: { cameraPolicy: "profile-fixed", canonicalPostprocess: true },
};

test("accepts only the registered suite structured payload", () => {
  const parsed = parseRequest(JSON.stringify({
    schema: "meshshot.browser-sidecar.render-request/2",
    program: "suite",
    payload: {
      viewer: { modelKey: "inspection-step", inspectionControl: "toggle-projection" },
      residual,
    },
  }));
  assert.equal(parsed.payload.residual.variant, "step");
});

test("rejects URL, JavaScript, path, environment, and unknown option keys", () => {
  for (const extra of ["url", "javascript", "path", "environment", "camera"] ) {
    assert.throws(() => parseRequest(JSON.stringify({
      schema: "meshshot.browser-sidecar.render-request/2",
      program: "residual",
      payload: { ...residual, [extra]: "forbidden" },
    })), /exact keys/);
  }
  assert.throws(() => parseRequest(JSON.stringify({
    schema: "meshshot.browser-sidecar.render-request/2",
    program: "residual",
    payload: { ...residual, options: { ...residual.options, executable: "/bin/sh" } },
  })), /exact keys/);
});

test("validates geometry, variant, and fixed camera options", () => {
  assert.throws(() => parseRequest(JSON.stringify({
    schema: "meshshot.browser-sidecar.render-request/2",
    program: "residual",
    payload: { ...residual, variant: "arbitrary" },
  })), /variant/);
  assert.throws(() => parseRequest(JSON.stringify({
    schema: "meshshot.browser-sidecar.render-request/2",
    program: "residual",
    payload: { ...residual, candidate: { vertices: [[0, 0, NaN]], faces: [[0, 0, 0]] } },
  })), /JSON|geometry/);
  assert.throws(() => parseRequest(JSON.stringify({
    schema: "meshshot.browser-sidecar.render-request/2",
    program: "residual",
    payload: { ...residual, options: { cameraPolicy: "caller-controlled", canonicalPostprocess: true } },
  })), /cameraPolicy/);
});

test("accepts bounded isolation markers and a structured model", () => {
  const parsed = parseRequest(JSON.stringify({
    schema: "meshshot.browser-sidecar.render-request/2",
    program: "hold",
    payload: { model: triangle, ownMarker: "job-a-marker", peerMarker: "job-b-marker" },
  }));
  assert.equal(parsed.payload.ownMarker, "job-a-marker");
});
