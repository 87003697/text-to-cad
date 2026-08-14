#!/usr/bin/env node
// THROWAWAY same-Colima comparison: mirrors the current in-process launch + route fulfillment.
import crypto from "node:crypto";
import fs from "node:fs";
import { chromium } from "playwright-core";

const ROOT = "/opt/browser-sidecar";
const CHROMIUM = "/ms-playwright/chromium-1223/chrome-linux64/chrome";
const profileBytes = fs.readFileSync(`${ROOT}/profile.json`);
const profile = JSON.parse(profileBytes.toString("utf8"));
const cube = {
  vertices: [[-0.5,-0.5,-0.5],[0.5,-0.5,-0.5],[0.5,0.5,-0.5],[-0.5,0.5,-0.5],[-0.5,-0.5,0.5],[0.5,-0.5,0.5],[0.5,0.5,0.5],[-0.5,0.5,0.5]],
  faces: [[0,2,1],[0,3,2],[4,5,6],[4,6,7],[0,4,7],[0,7,3],[1,2,6],[1,6,5],[0,1,5],[0,5,4],[3,7,6],[3,6,2]],
};
const sha256 = (buffer) => crypto.createHash("sha256").update(buffer).digest("hex");
const browser = await chromium.launch({
  executablePath: CHROMIUM,
  headless: true,
  args: ["--disable-dev-shm-usage", "--no-sandbox"],
});
try {
  const context = await browser.newContext({ viewport: { width: 1024, height: 768 }, deviceScaleFactor: 1 });
  const page = await context.newPage();
  await page.route("http://meshshot.local/**", async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    const file = pathname === "/render.html"
      ? `${ROOT}/residual/render.html`
      : pathname === "/residual-render.js" ? `${ROOT}/residual/residual-render.js` : "";
    if (!file) await route.fulfill({ status: 404, body: "not found" });
    else await route.fulfill({
      status: 200,
      contentType: file.endsWith(".html") ? "text/html; charset=utf-8" : "text/javascript; charset=utf-8",
      body: fs.readFileSync(file),
    });
  });
  await page.goto("http://meshshot.local/render.html", { waitUntil: "load", timeout: 60_000 });
  await page.waitForFunction("typeof window.__meshshotRender === 'function'", null, { timeout: 60_000 });
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
  process.stdout.write(`${JSON.stringify({
    ok: true,
    mode: "legacy-in-process-launch",
    pngSha256: sha256(png),
    pngBytes: png.length,
    profileSha256: sha256(profileBytes),
    views: render.views.map((view) => view.name),
  })}\n`);
  await context.close();
} finally {
  await browser.close();
}
