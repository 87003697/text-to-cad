"use strict";

const assert = require("node:assert/strict");
const http = require("node:http");
const test = require("node:test");

const {
  createCadRenderServer,
  validateRequest,
} = require("./cad-render-service.cjs");

const token = "a".repeat(64);
const programDigest = `sha256:${"b".repeat(64)}`;
const jobId = "c".repeat(32);
function encodeTypedArray(values) {
  return Buffer.from(values.buffer, values.byteOffset, values.byteLength).toString("base64");
}

const triangle = {
  schema: "text-to-cad.packed-triangle-mesh/1",
  vertexCount: 3,
  faceCount: 1,
  positionsF32LeBase64: encodeTypedArray(
    new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]),
  ),
  indicesU32LeBase64: encodeTypedArray(new Uint32Array([0, 1, 2])),
};

function request(port, body, authorization = `Bearer ${token}`) {
  return new Promise((resolve, reject) => {
    const encoded = Buffer.from(JSON.stringify(body));
    const operation = http.request(
      {
        host: "127.0.0.1",
        port,
        path: "/cad/render/residual",
        method: "POST",
        headers: {
          authorization,
          "content-type": "application/json",
          "content-length": String(encoded.length),
        },
      },
      (response) => {
        const chunks = [];
        response.on("data", (chunk) => chunks.push(chunk));
        response.on("end", () =>
          resolve({ status: response.statusCode, value: JSON.parse(Buffer.concat(chunks)) }),
        );
      },
    );
    operation.on("error", reject);
    operation.end(encoded);
  });
}

test("serves one closed residual operation", async (context) => {
  const expectedResult = { ok: true, pngDataUrl: "data:image/png;base64,AA==", views: [] };
  const server = createCadRenderServer({
    token,
    programDigest,
    jobId,
    assets: { html: Buffer.from("html"), javascript: Buffer.from("js") },
    render: async () => expectedResult,
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  context.after(() => new Promise((resolve) => server.close(resolve)));
  const port = server.address().port;
  const response = await request(port, {
    schema: "text-to-cad.cad-render-request/2",
    jobId,
    program: "residual",
    programDigest,
    payload: {
      reference: triangle,
      candidate: triangle,
      variant: "step",
      exteriorDirections: [],
      options: { cameraPolicy: "profile-fixed", canonicalPostprocess: true },
    },
  });
  assert.equal(response.status, 200);
  assert.deepEqual(response.value, {
    schema: "text-to-cad.cad-render-response/1",
    jobId,
    program: "residual",
    programDigest,
    result: expectedResult,
  });
});

test("rejects an invalid token and unknown request keys", async (context) => {
  const server = createCadRenderServer({
    token,
    programDigest,
    jobId,
    assets: { html: Buffer.from("html"), javascript: Buffer.from("js") },
    render: async () => assert.fail("render must not run"),
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  context.after(() => new Promise((resolve) => server.close(resolve)));
  const port = server.address().port;
  const unauthorized = await request(port, {}, "Bearer wrong");
  assert.equal(unauthorized.status, 401);
  const invalid = await request(port, {
    schema: "text-to-cad.cad-render-request/2",
    jobId,
    program: "residual",
    programDigest,
    payload: {},
    unexpected: true,
  });
  assert.equal(invalid.status, 400);
});

test("rejects a request owned by another browser job", async (context) => {
  const server = createCadRenderServer({
    token,
    programDigest,
    jobId,
    assets: { html: Buffer.from("html"), javascript: Buffer.from("js") },
    render: async () => assert.fail("render must not run"),
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  context.after(() => new Promise((resolve) => server.close(resolve)));
  const response = await request(server.address().port, {
    schema: "text-to-cad.cad-render-request/2",
    jobId: "d".repeat(32),
    program: "residual",
    programDigest,
    payload: {
      reference: triangle,
      candidate: triangle,
      variant: "step",
      exteriorDirections: [],
      options: { cameraPolicy: "profile-fixed", canonicalPostprocess: true },
    },
  });
  assert.equal(response.status, 400);
});

test("rejects malformed packed geometry before rendering", async (context) => {
  const server = createCadRenderServer({
    token,
    programDigest,
    jobId,
    assets: { html: Buffer.from("html"), javascript: Buffer.from("js") },
    render: async () => assert.fail("render must not run"),
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  context.after(() => new Promise((resolve) => server.close(resolve)));
  for (const invalidGeometry of [
    { ...triangle, indicesU32LeBase64: encodeTypedArray(new Uint32Array([0, 1, 3])) },
    { ...triangle, positionsF32LeBase64: "not-base64" },
    {
      ...triangle,
      positionsF32LeBase64: encodeTypedArray(
        new Float32Array([Number.NaN, 0, 0, 1, 0, 0, 0, 1, 0]),
      ),
    },
  ]) {
    const response = await request(server.address().port, {
      schema: "text-to-cad.cad-render-request/2",
      jobId,
      program: "residual",
      programDigest,
      payload: {
        reference: invalidGeometry,
        candidate: triangle,
        variant: "step",
        exteriorDirections: [],
        options: { cameraPolicy: "profile-fixed", canonicalPostprocess: true },
      },
    });
    assert.equal(response.status, 400);
  }
});

test("validates a representative packed mesh without regex stack growth", () => {
  const vertexCount = 1_000_000;
  const largeGeometry = {
    schema: "text-to-cad.packed-triangle-mesh/1",
    vertexCount,
    faceCount: 1,
    positionsF32LeBase64: encodeTypedArray(new Float32Array(vertexCount * 3)),
    indicesU32LeBase64: encodeTypedArray(new Uint32Array([0, 1, 2])),
  };
  const requestValue = {
    schema: "text-to-cad.cad-render-request/2",
    jobId,
    program: "residual",
    programDigest,
    payload: {
      reference: largeGeometry,
      candidate: triangle,
      variant: "step",
      exteriorDirections: [],
      options: { cameraPolicy: "profile-fixed", canonicalPostprocess: true },
    },
  };
  assert.equal(validateRequest(requestValue, programDigest, jobId), requestValue);
});

test("bounds a render that never settles and aborts its work", async (context) => {
  let aborted = false;
  const server = createCadRenderServer({
    token,
    programDigest,
    jobId,
    renderTimeoutMs: 20,
    assets: { html: Buffer.from("html"), javascript: Buffer.from("js") },
    render: async (_payload, signal) => {
      signal.addEventListener("abort", () => { aborted = true; }, { once: true });
      return new Promise(() => {});
    },
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  context.after(() => new Promise((resolve) => server.close(resolve)));
  const response = await request(server.address().port, {
    schema: "text-to-cad.cad-render-request/2",
    jobId,
    program: "residual",
    programDigest,
    payload: {
      reference: triangle,
      candidate: triangle,
      variant: "step",
      exteriorDirections: [],
      options: { cameraPolicy: "profile-fixed", canonicalPostprocess: true },
    },
  });
  assert.equal(response.status, 500);
  assert.equal(aborted, true);
});
