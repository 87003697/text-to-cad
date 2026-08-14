#!/usr/bin/env node
// THROWAWAY prototype. The outer container runtime owns this process and Chromium.
import crypto from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { spawn } from "node:child_process";
import { chromium } from "playwright-core";

const ROOT = "/opt/browser-sidecar";
const CHROMIUM = "/ms-playwright/chromium-1223/chrome-linux64/chrome";
const VIEWER_PORT = 4173;
const RESIDUAL_PORT = 4174;
const BROWSER_PORT = 3000;
const CONTROL_PORT = 3001;
const JOB_ID = String(process.env.BROWSER_SIDECAR_JOB_ID || "").trim();
if (!/^[a-z0-9][a-z0-9-]{0,47}$/.test(JOB_ID)) {
  throw new Error("BROWSER_SIDECAR_JOB_ID must be a bounded lowercase job id");
}

function sha256File(file) {
  return crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}

function staticServer(root, port) {
  const resolvedRoot = path.resolve(root);
  const server = http.createServer((request, response) => {
    const url = new URL(request.url || "/", "http://sidecar.local");
    const relative = url.pathname === "/" ? "render.html" : decodeURIComponent(url.pathname).replace(/^\/+/, "");
    const candidate = path.resolve(resolvedRoot, relative);
    if (request.method !== "GET" || !(candidate === resolvedRoot || candidate.startsWith(`${resolvedRoot}${path.sep}`)) || !fs.existsSync(candidate)) {
      response.writeHead(request.method === "GET" ? 404 : 405, { "content-type": "text/plain" });
      response.end("not found");
      return;
    }
    const contentType = candidate.endsWith(".html")
      ? "text/html; charset=utf-8"
      : candidate.endsWith(".js") ? "text/javascript; charset=utf-8" : "application/octet-stream";
    response.writeHead(200, { "content-type": contentType, "cache-control": "no-store" });
    fs.createReadStream(candidate).pipe(response);
  });
  server.listen(port, "127.0.0.1");
  return server;
}

function waitForViewer() {
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + 30_000;
    const probe = () => {
      const request = http.get(`http://127.0.0.1:${VIEWER_PORT}/`, (response) => {
        response.resume();
        if (response.statusCode === 200) resolve();
        else if (Date.now() < deadline) setTimeout(probe, 100);
        else reject(new Error(`viewer returned ${response.statusCode}`));
      });
      request.on("error", () => {
        if (Date.now() < deadline) setTimeout(probe, 100);
        else reject(new Error("viewer did not become ready"));
      });
    };
    probe();
  });
}

const viewer = spawn("node", [
  `${ROOT}/viewer/src/server/server.mjs`,
  "--host", "127.0.0.1",
  "--port", String(VIEWER_PORT),
  "--dir", `${ROOT}/fixtures`,
], {
  stdio: ["ignore", "inherit", "inherit"],
  env: {
    ...process.env,
    VIEWER_ASSET_BACKEND: "local-fs",
    VIEWER_DEFAULT_FILE: "browser_sidecar_inspection.step",
    VIEWER_GIT: "prototype-baked-assets",
  },
});
const residualServer = staticServer(`${ROOT}/residual`, RESIDUAL_PORT);
await waitForViewer();

const browserServer = await chromium.launchServer({
  executablePath: CHROMIUM,
  headless: true,
  host: "0.0.0.0",
  port: BROWSER_PORT,
  args: ["--disable-dev-shm-usage", "--no-sandbox"],
});
const endpointPath = new URL(browserServer.wsEndpoint()).pathname;
const programDigests = Object.freeze({
  viewer: sha256File(`${ROOT}/viewer/dist/index.html`),
  residual: sha256File(`${ROOT}/residual/residual-render.js`),
});
const forbiddenSourceAliases = [
  "/workspace",
  "/repo",
  "/source",
  "/Users/zhiyuanma",
  "/private/tmp/text-to-cad-browser-sidecar-prototype-20260815",
];

const control = http.createServer((request, response) => {
  if (request.method !== "GET" || request.url !== "/v1/authority") {
    response.writeHead(404, { "content-type": "application/json" });
    response.end(JSON.stringify({ error: "not found" }));
    return;
  }
  response.writeHead(200, { "content-type": "application/json", "cache-control": "no-store" });
  response.end(JSON.stringify({
    schema: "meshshot.browser-sidecar.prototype/1",
    jobId: JOB_ID,
    endpointPath,
    browserPid: browserServer.process()?.pid ?? null,
    chromiumRevision: "1223",
    chromiumVersion: "148.0.7778.96",
    playwrightVersion: "1.60.0",
    programs: programDigests,
    sourceAliasesVisible: forbiddenSourceAliases.filter((candidate) => fs.existsSync(candidate)),
  }));
});
control.listen(CONTROL_PORT, "0.0.0.0", () => {
  process.stdout.write(`${JSON.stringify({ event: "ready", jobId: JOB_ID, endpointPath, programs: programDigests })}\n`);
});

let closing = false;
async function close(reason) {
  if (closing) return;
  closing = true;
  process.stdout.write(`${JSON.stringify({ event: "closing", jobId: JOB_ID, reason })}\n`);
  await new Promise((resolve) => control.close(resolve));
  await browserServer.close();
  await new Promise((resolve) => residualServer.close(resolve));
  viewer.kill("SIGTERM");
  await new Promise((resolve) => {
    const timer = setTimeout(resolve, 2_000);
    viewer.once("exit", () => { clearTimeout(timer); resolve(); });
  });
}
for (const signal of ["SIGTERM", "SIGINT"]) {
  process.on(signal, () => close(signal).then(() => process.exit(0)));
}
viewer.once("exit", (code, signal) => {
  if (!closing) close(`viewer-exit:${code ?? ""}:${signal ?? ""}`).then(() => process.exit(1));
});
