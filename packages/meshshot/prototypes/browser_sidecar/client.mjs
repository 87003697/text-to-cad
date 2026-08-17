#!/usr/bin/env node
// THROWAWAY sealed Render Program client. It does not accept URL, JS, argv or executable inputs.
import crypto from "node:crypto";
import fs from "node:fs";
import { chromium } from "playwright-core";
import { parseRequest } from "./contract.mjs";

if (process.argv.length !== 2) throw new Error("client accepts one structured JSON request on stdin only");
const requestText = fs.readFileSync(0, "utf8");
const request = parseRequest(requestText);
const { program } = request;
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
        const viewerPayload = program === "suite" ? request.payload.viewer : request.payload;
        await page.goto("http://127.0.0.1:4173/?file=browser_sidecar_inspection.step", { waitUntil: "networkidle", timeout: 60_000 });
        await page.waitForTimeout(2_000);
        if (viewerPayload.inspectionControl !== "toggle-projection") throw new Error("unregistered inspection control");
        const inspectionBefore = await page.evaluate(async () => {
          const selector = 'button[aria-label^="Display and projection:"]';
          const deadline = Date.now() + 25_000;
          let control = null;
          while (Date.now() < deadline) {
            control = document.querySelector(selector);
            if (control) break;
            await new Promise((resolve) => setTimeout(resolve, 50));
          }
          const ariaLabel = control?.getAttribute("aria-label") || null;
          if (!(control instanceof HTMLButtonElement) || ariaLabel !== "Display and projection: Solid, Orthographic") {
            throw new Error(`unexpected projection control: ${ariaLabel}`);
          }
          control.focus();
          if (document.activeElement !== control) throw new Error("projection control did not accept focus");
          return ariaLabel;
        });
        const inspectionTarget = "Perspective";
        await page.keyboard.press("Enter");
        await page.evaluate(async (target) => {
          const deadline = Date.now() + 5_000;
          let menuItem = null;
          while (Date.now() < deadline) {
            menuItem = [...document.querySelectorAll('[role="menuitem"]')]
              .find((element) => element.textContent?.trim().startsWith(target));
            if (menuItem) break;
            await new Promise((resolve) => setTimeout(resolve, 50));
          }
          if (!(menuItem instanceof HTMLElement)) throw new Error(`${target} menu item did not appear`);
          menuItem.focus();
          if (document.activeElement !== menuItem) throw new Error(`${target} menu item did not accept focus`);
        }, inspectionTarget);
        await page.keyboard.press("Enter");
        const inspectionAfter = await page.evaluate(async (target) => {
          const selector = 'button[aria-label^="Display and projection:"]';
          const expected = `Display and projection: Solid, ${target}`;
          const deadline = Date.now() + 5_000;
          let ariaLabel = null;
          while (Date.now() < deadline) {
            ariaLabel = document.querySelector(selector)?.getAttribute("aria-label") || null;
            if (ariaLabel === expected) return ariaLabel;
            await new Promise((resolve) => setTimeout(resolve, 50));
          }
          throw new Error(`projection control did not reach ${expected}: ${ariaLabel}`);
        }, inspectionTarget);
        const screenshot = await page.screenshot({ type: "png", timeout: 120_000 });
        const body = await page.locator("body").innerText();
        results[requestedProgram] = {
          title: await page.title(),
          screenshotSha256: sha256(screenshot),
          screenshotBytes: screenshot.length,
          bodyMentionsFixture: /browser_sidecar_inspection\.step/i.test(body),
          bodyHasArtifactError: /STEP artifact missing|Generated GLB is missing/i.test(body),
          bodyExcerpt: body.slice(0, 300),
          programDigest: authority.programs.viewer,
          modelKey: viewerPayload.modelKey,
          inspection: {
            control: viewerPayload.inspectionControl,
            before: inspectionBefore,
            target: inspectionTarget,
            after: inspectionAfter,
            changed: inspectionBefore !== inspectionAfter && String(inspectionAfter).includes(inspectionTarget),
          },
        };
      } else if (requestedProgram === "residual") {
        const residualPayload = program === "suite" ? request.payload.residual : request.payload;
        await page.goto("http://127.0.0.1:4174/render.html", { waitUntil: "load", timeout: 60_000 });
        await page.waitForFunction("typeof window.__meshshotRender === 'function'", null, { timeout: 60_000 });
        const profileResponse = await fetch(`http://${sidecarHost}:3001/v1/authority`);
        if (!profileResponse.ok) throw new Error("sidecar authority disappeared");
        const profileBytes = fs.readFileSync("/opt/browser-sidecar/profile.json");
        const profile = JSON.parse(profileBytes.toString("utf8"));
        const render = await page.evaluate((payload) => window.__meshshotRender(payload), {
          profile,
          variant: residualPayload.variant,
          reference: residualPayload.reference,
          candidate: residualPayload.candidate,
          exteriorDirections: residualPayload.exteriorDirections,
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
          requestOptions: residualPayload.options,
        };
      } else {
        const { model, ownMarker, peerMarker } = request.payload;
        const consoleMessages = [];
        page.on("console", (message) => consoleMessages.push(message.text()));
        await context.addCookies([{ name: "job-marker", value: ownMarker, url: "http://127.0.0.1:4174" }]);
        await page.goto("http://127.0.0.1:4174/render.html", { waitUntil: "load" });
        await page.waitForFunction("typeof window.__meshshotRender === 'function'");
        await page.evaluate(({ ownMarker }) => {
          document.body.dataset.jobMarker = ownMarker;
          localStorage.setItem("job-marker", ownMarker);
          console.log(`job-marker:${ownMarker}`);
        }, { ownMarker });
        const profile = JSON.parse(fs.readFileSync("/opt/browser-sidecar/profile.json", "utf8"));
        const rendered = await page.evaluate((payload) => window.__meshshotRender(payload), {
          profile,
          variant: "step",
          reference: model,
          candidate: model,
          exteriorDirections: [],
        });
        if (!rendered?.ok) throw new Error(rendered?.error || "hold model render failed");
        const modelPng = Buffer.from(rendered.pngDataUrl.split(",", 2)[1], "base64");
        const filesystemPath = "/tmp/browser-sidecar-isolation-marker";
        fs.writeFileSync(filesystemPath, ownMarker, { encoding: "utf8", mode: 0o600 });
        const pageMarker = await page.evaluate(() => document.body.dataset.jobMarker);
        const storageMarker = await page.evaluate(() => localStorage.getItem("job-marker"));
        const cookies = await context.cookies();
        const cookieValues = cookies.filter(({ name }) => name === "job-marker").map(({ value }) => value);
        const filesystemMarker = fs.readFileSync(filesystemPath, "utf8");
        const isolation = {
          ownMarker,
          peerMarker,
          modelInputSha256: sha256(Buffer.from(JSON.stringify(model))),
          modelPngSha256: sha256(modelPng),
          pageOwn: pageMarker === ownMarker,
          pagePeerAbsent: pageMarker !== peerMarker,
          localStorageOwn: storageMarker === ownMarker,
          localStoragePeerAbsent: storageMarker !== peerMarker,
          cookieOwn: cookieValues.length === 1 && cookieValues[0] === ownMarker,
          cookiePeerAbsent: !cookieValues.includes(peerMarker),
          consoleOwn: consoleMessages.includes(`job-marker:${ownMarker}`),
          consolePeerAbsent: !consoleMessages.some((message) => message.includes(peerMarker)),
          filesystemOwn: filesystemMarker === ownMarker,
          filesystemPeerAbsent: filesystemMarker !== peerMarker,
        };
        process.stdout.write(`${JSON.stringify({ event: "hold-ready", jobId, isolation })}\n`);
        await page.waitForTimeout(6_000);
        results[requestedProgram] = { isolation, jobId };
      }
    } finally {
      await context.close();
    }
  }
  result = program === "suite" ? results : results[program];
} finally {
  await browser.close();
}
process.stdout.write(`${JSON.stringify({
  ok: true,
  schema: request.schema,
  requestSha256: sha256(Buffer.from(requestText)),
  program,
  jobId,
  result,
})}\n`);
