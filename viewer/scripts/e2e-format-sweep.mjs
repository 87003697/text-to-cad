#!/usr/bin/env node
// Load one fixture per render format through a running CAD Viewer and assert each one
// actually draws something, with no page errors.
//
// This is the standing gate for viewer work that touches shared code: the whole point of
// the capability registry is that a change to one format reaches the others, which also
// means a mistake in shared code breaks all of them at once. Screenshot-based because a
// blank-but-error-free viewport is the signature failure here — a shader that fails to
// compile, or a gate that hides the geometry, both throw nothing.
//
// Reads the REAL framebuffer via page.screenshot(). Do NOT sample the canvas with
// drawImage: the drawing buffer is not preserved, so every format reports blank.
//
// Usage:
//   node viewer/scripts/e2e-format-sweep.mjs --dir <models-root> [--url http://127.0.0.1:3245]
//                                            [--all-implicits] [--out <dir>]
//
// Requires a viewer already serving <models-root> (npm --prefix viewer run start) and
// playwright available. Exits non-zero on the first failing format.

import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);

function parseArgs(argv) {
  const args = { url: "http://127.0.0.1:3245", dir: "", out: "", allImplicits: false };
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    if (flag === "--url") args.url = argv[++index] || args.url;
    else if (flag === "--dir") args.dir = argv[++index] || "";
    else if (flag === "--out") args.out = argv[++index] || "";
    else if (flag === "--all-implicits") args.allImplicits = true;
  }
  return args;
}

// One fixture per format family. Kept small on purpose: this runs on every shared-code
// change, so it has to stay fast enough that people actually run it.
const FIXTURES = [
  { format: "stl", file: "fun/miniature_spiral_staircase_highres.stl" },
  { format: "3mf", file: "fun/miniature_spiral_staircase_highres.3mf" },
  { format: "glb", file: "fun/miniature_spiral_staircase_highres.glb" },
  { format: "step", file: "simple/cam_follower_roller.step.py" },
  { format: "dxf", file: "dxf/alu_extrusion_profile.dxf" },
  { format: "implicit", file: "implicits/rounded-orb.implicit.js" }
];

// Below this fraction of non-background pixels the viewport is effectively empty.
const MIN_COVERAGE = 0.005;
const VIEWPORT = { width: 1440, height: 900 };
const CLIP = { x: 40, y: 60, width: 900, height: 760 };

function coverage(png) {
  const background = [png.data[0], png.data[1], png.data[2]];
  let covered = 0;
  let total = 0;
  for (let index = 0; index < png.data.length; index += 44) {
    total += 1;
    const delta = Math.abs(png.data[index] - background[0]) +
      Math.abs(png.data[index + 1] - background[1]) +
      Math.abs(png.data[index + 2] - background[2]);
    if (delta > 24) covered += 1;
  }
  return total ? covered / total : 0;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (!args.dir) {
    console.error("--dir <models-root> is required (absolute path the viewer is serving)");
    process.exit(2);
  }
  const modelsRoot = path.resolve(args.dir);
  const { chromium } = require("playwright");
  const { PNG } = require("pngjs");

  const fixtures = [...FIXTURES];
  if (args.allImplicits) {
    const implicitDir = path.join(modelsRoot, "implicits");
    if (fs.existsSync(implicitDir)) {
      for (const name of fs.readdirSync(implicitDir).filter((f) => f.endsWith(".implicit.js"))) {
        const file = `implicits/${name}`;
        if (!fixtures.some((fixture) => fixture.file === file)) {
          fixtures.push({ format: "implicit", file });
        }
      }
    }
  }

  // --use-angle=metal: the software rasteriser shades differently and hides real GPU
  // failures, so a sweep run under SwiftShader is not evidence.
  const browser = await chromium.launch({ args: ["--use-angle=metal", "--ignore-gpu-blocklist"] });
  const page = await browser.newPage({ viewport: VIEWPORT, deviceScaleFactor: 1 });
  const results = [];

  for (const fixture of fixtures) {
    const errors = [];
    const onPageError = (error) => errors.push(String(error).slice(0, 200));
    const onConsole = (message) => {
      if (message.type() === "error") errors.push(message.text().slice(0, 200));
    };
    page.on("pageerror", onPageError);
    page.on("console", onConsole);

    // The model root is the URL PATH for this backend; a bare ?dir= alone resolves nothing.
    const url = `${args.url}${modelsRoot}?dir=${encodeURIComponent(modelsRoot)}` +
      `&file=${encodeURIComponent(fixture.file)}`;
    await page.goto(url, { waitUntil: "domcontentloaded" });
    // STEP and DXF build their package on first open; give the slowest path room.
    await page.waitForTimeout(fixture.format === "step" ? 14000 : 9000);

    const buffer = await page.screenshot({ clip: CLIP });
    if (args.out) {
      fs.mkdirSync(args.out, { recursive: true });
      fs.writeFileSync(path.join(args.out, `${fixture.file.replace(/[^a-z0-9]/gi, "_")}.png`), buffer);
    }
    const covered = coverage(PNG.sync.read(buffer));
    results.push({
      format: fixture.format,
      file: fixture.file,
      coverage: Number(covered.toFixed(4)),
      blank: covered < MIN_COVERAGE,
      errors: errors.slice(0, 3)
    });

    page.off("pageerror", onPageError);
    page.off("console", onConsole);
  }

  await browser.close();

  const failures = results.filter((result) => result.blank || result.errors.length);
  for (const result of results) {
    const status = result.blank ? "BLANK" : result.errors.length ? "ERRORS" : "ok";
    console.log(`${status.padEnd(6)} ${result.format.padEnd(9)} cov=${result.coverage} ${result.file}`);
    for (const error of result.errors) console.log(`         ${error}`);
  }
  console.log(`\n${results.length - failures.length}/${results.length} formats rendered`);
  process.exit(failures.length ? 1 : 0);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
