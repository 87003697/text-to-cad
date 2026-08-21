"use strict";

const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");

const REQUEST_SCHEMA = "text-to-cad.cad-render-request/2";
const RESPONSE_SCHEMA = "text-to-cad.cad-render-response/1";
const MAX_REQUEST_BYTES = 96 * 1024 * 1024;
const MAX_RESPONSE_BYTES = 16 * 1024 * 1024;
const DEFAULT_PORT = 9224;
const DEFAULT_REQUEST_BODY_TIMEOUT_MS = 10_000;
const DEFAULT_RENDER_TIMEOUT_MS = 110_000;
const DEFAULT_PROGRAM_ROOT = "/opt/text-to-cad/render-programs/residual";
const DEFAULT_CHROME =
  "/ms-playwright/chromium_headless_shell-1161/chrome-linux/headless_shell";

function exactKeys(value, keys) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const observed = Object.keys(value).sort();
  const expected = [...keys].sort();
  return observed.length === expected.length && observed.every((key, i) => key === expected[i]);
}

function canonicalBase64(value, byteLength) {
  if (typeof value !== "string" || value.length !== 4 * Math.ceil(byteLength / 3)) {
    return false;
  }
  const padding = (3 - (byteLength % 3)) % 3;
  const encodedLength = value.length - padding;
  for (let index = 0; index < encodedLength; index += 1) {
    const code = value.charCodeAt(index);
    const alphaNumeric =
      (code >= 65 && code <= 90) ||
      (code >= 97 && code <= 122) ||
      (code >= 48 && code <= 57);
    if (!alphaNumeric && code !== 43 && code !== 47) return false;
  }
  for (let index = encodedLength; index < value.length; index += 1) {
    if (value.charCodeAt(index) !== 61) return false;
  }
  return Buffer.byteLength(value, "base64") === byteLength;
}

function validGeometry(value) {
  if (!exactKeys(value, [
    "schema",
    "vertexCount",
    "faceCount",
    "positionsF32LeBase64",
    "indicesU32LeBase64",
  ])) return false;
  const vertexCount = value.vertexCount;
  const faceCount = value.faceCount;
  if (
    value.schema !== "text-to-cad.packed-triangle-mesh/1" ||
    !Number.isSafeInteger(vertexCount) || vertexCount <= 0 ||
    !Number.isSafeInteger(faceCount) || faceCount <= 0 ||
    vertexCount > Math.floor(MAX_REQUEST_BYTES / 12) ||
    faceCount > Math.floor(MAX_REQUEST_BYTES / 12)
  ) return false;
  const positionBytes = vertexCount * 3 * 4;
  const indexBytes = faceCount * 3 * 4;
  if (
    !canonicalBase64(value.positionsF32LeBase64, positionBytes) ||
    !canonicalBase64(value.indicesU32LeBase64, indexBytes)
  ) return false;

  const positions = Buffer.from(value.positionsF32LeBase64, "base64");
  for (let offset = 0; offset < positions.length; offset += 4) {
    if (!Number.isFinite(positions.readFloatLE(offset))) return false;
  }
  const indices = Buffer.from(value.indicesU32LeBase64, "base64");
  for (let offset = 0; offset < indices.length; offset += 4) {
    if (indices.readUInt32LE(offset) >= vertexCount) return false;
  }
  return true;
}

function validateRequest(value, programDigest, jobId) {
  if (!exactKeys(value, ["schema", "jobId", "program", "programDigest", "payload"])) {
    throw new Error("request schema is invalid");
  }
  if (
    value.schema !== REQUEST_SCHEMA ||
    typeof value.jobId !== "string" ||
    value.jobId !== jobId ||
    value.program !== "residual" ||
    value.programDigest !== programDigest
  ) {
    throw new Error("request identity is invalid");
  }
  const payload = value.payload;
  if (
    !exactKeys(payload, ["reference", "candidate", "variant", "exteriorDirections", "options"]) ||
    !validGeometry(payload.reference) ||
    !validGeometry(payload.candidate) ||
    !["step", "final"].includes(payload.variant) ||
    !Array.isArray(payload.exteriorDirections) ||
    payload.exteriorDirections.length > 6 ||
    new Set(payload.exteriorDirections).size !== payload.exteriorDirections.length ||
    !payload.exteriorDirections.every((item) => ["-x", "+x", "-y", "+y", "-z", "+z"].includes(item)) ||
    !exactKeys(payload.options, ["cameraPolicy", "canonicalPostprocess"]) ||
    payload.options.cameraPolicy !== "profile-fixed" ||
    payload.options.canonicalPostprocess !== true
  ) {
    throw new Error("residual payload is invalid");
  }
  return value;
}

function jsonResponse(response, status, value) {
  const body = Buffer.from(JSON.stringify(value));
  if (body.length > MAX_RESPONSE_BYTES) {
    response.writeHead(500, { "content-type": "application/json", connection: "close" });
    response.end(JSON.stringify({ ok: false, error: "render response is too large" }));
    return;
  }
  response.writeHead(status, {
    "content-type": "application/json",
    "content-length": String(body.length),
    "cache-control": "no-store",
    connection: "close",
  });
  response.end(body);
}

async function renderWithDeadline(render, payload, timeoutMs) {
  const controller = new AbortController();
  let timer;
  const deadline = new Promise((_, reject) => {
    timer = setTimeout(() => {
      controller.abort();
      reject(new Error("render deadline exceeded"));
    }, timeoutMs);
  });
  try {
    return await Promise.race([render(payload, controller.signal), deadline]);
  } finally {
    clearTimeout(timer);
  }
}

function createCadRenderServer({
  token,
  programDigest,
  jobId,
  render,
  assets,
  requestBodyTimeoutMs = DEFAULT_REQUEST_BODY_TIMEOUT_MS,
  renderTimeoutMs = DEFAULT_RENDER_TIMEOUT_MS,
}) {
  if (!/^[0-9a-f]{64}$/.test(token)) throw new Error("CAD render token is invalid");
  if (!/^sha256:[0-9a-f]{64}$/.test(programDigest)) {
    throw new Error("CAD render program digest is invalid");
  }
  if (!/^[0-9a-f]{12,64}$/.test(jobId)) throw new Error("browser runtime job ID is invalid");
  if (!Number.isSafeInteger(requestBodyTimeoutMs) || requestBodyTimeoutMs <= 0) {
    throw new Error("request body timeout is invalid");
  }
  if (!Number.isSafeInteger(renderTimeoutMs) || renderTimeoutMs <= 0) {
    throw new Error("render timeout is invalid");
  }
  return http.createServer((request, response) => {
    if (request.method === "GET" && request.url === "/healthz") {
      jsonResponse(response, 200, {
        schema: "text-to-cad.cad-render-health/1",
        program: "residual",
        programDigest,
      });
      return;
    }
    if (request.method === "GET" && request.url === "/cad/assets/render.html") {
      response.writeHead(200, {
        "content-type": "text/html; charset=utf-8",
        "cache-control": "no-store",
      });
      response.end(assets.html);
      return;
    }
    if (request.method === "GET" && request.url === "/residual-render.js") {
      response.writeHead(200, {
        "content-type": "text/javascript; charset=utf-8",
        "cache-control": "no-store",
      });
      response.end(assets.javascript);
      return;
    }
    if (request.method !== "POST" || request.url !== "/cad/render/residual") {
      jsonResponse(response, 404, { ok: false, error: "not found" });
      return;
    }
    if (request.headers.authorization !== `Bearer ${token}`) {
      jsonResponse(response, 401, { ok: false, error: "unauthorized" });
      return;
    }
    if (request.headers["content-type"] !== "application/json") {
      jsonResponse(response, 415, { ok: false, error: "unsupported media type" });
      return;
    }
    const declaredLength = Number(request.headers["content-length"]);
    if (
      request.headers["content-length"] !== undefined &&
      (!Number.isSafeInteger(declaredLength) || declaredLength < 0 || declaredLength > MAX_REQUEST_BYTES)
    ) {
      jsonResponse(response, 413, { ok: false, error: "request is too large" });
      request.resume();
      return;
    }
    let size = 0;
    const chunks = [];
    let bodyFinished = false;
    const bodyTimer = setTimeout(() => {
      if (bodyFinished) return;
      bodyFinished = true;
      jsonResponse(response, 408, { ok: false, error: "request body timed out" });
      request.destroy();
    }, requestBodyTimeoutMs);
    request.on("data", (chunk) => {
      if (bodyFinished) return;
      size += chunk.length;
      if (size > MAX_REQUEST_BYTES) {
        bodyFinished = true;
        clearTimeout(bodyTimer);
        jsonResponse(response, 413, { ok: false, error: "request is too large" });
        request.destroy();
        return;
      }
      chunks.push(chunk);
    });
    request.on("end", async () => {
      if (bodyFinished) return;
      bodyFinished = true;
      clearTimeout(bodyTimer);
      let value;
      try {
        value = validateRequest(
          JSON.parse(Buffer.concat(chunks).toString("utf8")),
          programDigest,
          jobId,
        );
      } catch {
        jsonResponse(response, 400, { ok: false, error: "invalid request" });
        return;
      }
      try {
        const result = await renderWithDeadline(render, value.payload, renderTimeoutMs);
        jsonResponse(response, 200, {
          schema: RESPONSE_SCHEMA,
          jobId: value.jobId,
          program: "residual",
          programDigest,
          result,
        });
      } catch {
        jsonResponse(response, 500, { ok: false, error: "render failed" });
      }
    });
    request.on("close", () => clearTimeout(bodyTimer));
  });
}

async function startProductionService() {
  const token = process.env.TTC_CAD_RENDER_TOKEN || "";
  const programDigest = process.env.TTC_CAD_RENDER_PROGRAM_DIGEST || "";
  const jobId = process.env.TTC_BROWSER_RUNTIME_JOB_ID || "";
  const host = process.env.TTC_CAD_RENDER_HOST || "0.0.0.0";
  const port = Number(process.env.TTC_CAD_RENDER_PORT || DEFAULT_PORT);
  const programRoot = process.env.TTC_CAD_RENDER_PROGRAM_ROOT || DEFAULT_PROGRAM_ROOT;
  const profilePath =
    process.env.TTC_CAD_RENDER_PROFILE_PATH ||
    path.join(programRoot, "cadena_residual_eight_view_v1.json");
  const chromeExecutable = process.env.PW_MCP_EXECUTABLE_PATH || DEFAULT_CHROME;
  const assets = {
    html: fs.readFileSync(path.join(programRoot, "render.html")),
    javascript: fs.readFileSync(path.join(programRoot, "residual-render.js")),
  };
  const profile = JSON.parse(fs.readFileSync(profilePath, "utf8"));
  const { chromium } = require("playwright-core");
  const browser = await chromium.launch({
    executablePath: chromeExecutable,
    headless: true,
    args: ["--no-sandbox"],
  });
  const render = async (payload, signal) => {
    const context = await browser.newContext({
      viewport: { width: 64, height: 64 },
      deviceScaleFactor: 1,
    });
    const abort = () => void context.close().catch(() => {});
    signal.addEventListener("abort", abort, { once: true });
    try {
      const page = await context.newPage();
      await page.goto(`http://127.0.0.1:${port}/cad/assets/render.html`, {
        waitUntil: "load",
        timeout: DEFAULT_RENDER_TIMEOUT_MS,
      });
      await page.waitForFunction("typeof window.__meshshotRender === 'function'", null, {
        timeout: DEFAULT_RENDER_TIMEOUT_MS,
      });
      return await page.evaluate(
        (renderPayload) => window.__meshshotRender(renderPayload),
        { ...payload, profile },
      );
    } finally {
      signal.removeEventListener("abort", abort);
      await context.close();
    }
  };
  const server = createCadRenderServer({ token, programDigest, jobId, render, assets });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, host, resolve);
  });
  process.stderr.write(`[cad-render] listening on ${host}:${port}\n`);
  const shutdown = async () => {
    server.close();
    await browser.close();
  };
  process.once("SIGINT", () => void shutdown().finally(() => process.exit(0)));
  process.once("SIGTERM", () => void shutdown().finally(() => process.exit(0)));
}

module.exports = {
  REQUEST_SCHEMA,
  RESPONSE_SCHEMA,
  createCadRenderServer,
  renderWithDeadline,
  validateRequest,
};

if (require.main === module) {
  startProductionService().catch((error) => {
    process.stderr.write(`[cad-render] startup failed: ${error.message}\n`);
    process.exit(1);
  });
}
