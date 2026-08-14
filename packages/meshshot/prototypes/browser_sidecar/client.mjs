#!/usr/bin/env node
// THROWAWAY sealed Render Program client. It does not accept URL, JS, argv or executable inputs.
import crypto from "node:crypto";
import fs from "node:fs";
import { chromium } from "playwright-core";

const PROGRAMS = new Set(["suite", "probe", "viewer", "residual", "hold"]);
const program = process.argv[2] || "";
if (!PROGRAMS.has(program) || process.argv.length !== 3) {
  throw new Error("usage: client.mjs <suite|probe|viewer|residual|hold>");
}
const jobId = String(process.env.BROWSER_SIDECAR_JOB_ID || "").trim();
const sidecarHost = String(process.env.BROWSER_SIDECAR_HOST || "sidecar").trim();
if (!/^[a-z0-9][a-z0-9-]{0,47}$/.test(jobId) || !/^[a-z0-9][a-z0-9-]{0,47}$/.test(sidecarHost)) {
  throw new Error("sealed job/host identity is invalid");
}

function sha256(buffer) {
  return crypto.createHash("sha256").update(buffer).digest("hex");
}
function browserExecutablesVisible() {
  const candidates = [
    "/ms-playwright",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
    "/opt/google/chrome",
  ];
  return candidates.filter((candidate) => fs.existsSync(candidate));
}
async function externalEgressBlocked() {
  try {
    await fetch("https://example.com/", { signal: AbortSignal.timeout(5_000) });
    return false;
  } catch {
    return true;
  }
}

const authorityResponse = await fetch(`http://${sidecarHost}:3001/v1/authority`);
if (!authorityResponse.ok) throw new Error(`authority endpoint returned ${authorityResponse.status}`);
const authority = await authorityResponse.json();
if (authority.jobId !== jobId || authority.schema !== "meshshot.browser-sidecar.prototype/1") {
  throw new Error("sidecar authority identity mismatch");
}
const endpoint = `ws://${sidecarHost}:3000${authority.endpointPath}`;
const browser = await chromium.connect(endpoint);
let result;
try {
  const requestedPrograms = program === "suite" ? ["probe", "viewer", "residual"] : [program];
  const results = {};
  for (const requestedProgram of requestedPrograms) {
    const context = await browser.newContext({ viewport: { width: 1024, height: 768 }, deviceScaleFactor: 1 });
    const page = await context.newPage();
    page.setDefaultTimeout(120_000);
    try {
      if (requestedProgram === "probe") {
        await page.goto("http://127.0.0.1:4174/render.html", { waitUntil: "load" });
        results[requestedProgram] = {
          connected: true,
          browserExecutablesVisible: browserExecutablesVisible(),
          contextCount: browser.contexts().length,
          pageCount: context.pages().length,
          sourceAliasesVisible: authority.sourceAliasesVisible,
          externalEgressBlocked: await externalEgressBlocked(),
        };
      } else if (requestedProgram === "viewer") {
        await page.goto("http://127.0.0.1:4173/?file=cube.stl", { waitUntil: "networkidle", timeout: 60_000 });
        await page.waitForTimeout(2_000);
        const screenshot = await page.screenshot({ type: "png", timeout: 120_000 });
        const body = await page.locator("body").innerText();
        results[requestedProgram] = {
          title: await page.title(),
          screenshotSha256: sha256(screenshot),
          screenshotBytes: screenshot.length,
          bodyMentionsFixture: /cube\.stl/i.test(body),
          bodyExcerpt: body.slice(0, 300),
          programDigest: authority.programs.viewer,
        };
      } else if (requestedProgram === "residual") {
        await page.goto("http://127.0.0.1:4174/render.html", { waitUntil: "load", timeout: 60_000 });
        await page.waitForFunction("typeof window.__meshshotRender === 'function'", null, { timeout: 60_000 });
        const profileResponse = await fetch(`http://${sidecarHost}:3001/v1/authority`);
        if (!profileResponse.ok) throw new Error("sidecar authority disappeared");
        const profileBytes = fs.readFileSync("/opt/browser-sidecar/profile.json");
        const profile = JSON.parse(profileBytes.toString("utf8"));
        const cube = {
          vertices: [[-0.5,-0.5,-0.5],[0.5,-0.5,-0.5],[0.5,0.5,-0.5],[-0.5,0.5,-0.5],[-0.5,-0.5,0.5],[0.5,-0.5,0.5],[0.5,0.5,0.5],[-0.5,0.5,0.5]],
          faces: [[0,2,1],[0,3,2],[4,5,6],[4,6,7],[0,4,7],[0,7,3],[1,2,6],[1,6,5],[0,1,5],[0,5,4],[3,7,6],[3,6,2]],
        };
        const render = await page.evaluate((payload) => window.__meshshotRender(payload), {
          profile,
          variant: "step",
          reference: cube,
          candidate: cube,
          exteriorDirections: [],
        });
        if (!render?.ok || !String(render.pngDataUrl || "").startsWith("data:image/png;base64,")) {
          throw new Error(render?.error || "invalid residual result");
        }
        const png = Buffer.from(render.pngDataUrl.split(",", 2)[1], "base64");
        results[requestedProgram] = {
          pngSha256: sha256(png),
          pngBytes: png.length,
          profileSha256: sha256(profileBytes),
          views: render.views,
          programDigest: authority.programs.residual,
        };
      } else {
        await context.addCookies([{ name: `job-${jobId}`, value: jobId, url: "http://127.0.0.1:4174" }]);
        await page.goto("http://127.0.0.1:4174/render.html", { waitUntil: "load" });
        process.stdout.write(`${JSON.stringify({ event: "hold-ready", jobId })}\n`);
        await page.waitForTimeout(6_000);
        const cookies = await context.cookies();
        results[requestedProgram] = { cookies: cookies.map(({ name, value }) => ({ name, value })), jobId };
      }
    } finally {
      await context.close();
    }
  }
  result = program === "suite" ? results : results[program];
} finally {
  await browser.close();
}
process.stdout.write(`${JSON.stringify({ ok: true, program, jobId, result })}\n`);
