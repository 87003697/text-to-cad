#!/usr/bin/env node
/**
 * The DXF drawing package's Node builder: `drawing.dxf` in, `preview.glb` out.
 *
 * Spawned by `cadgen._internal.node_runtime.run_node_builder` from INSIDE the Python
 * producer's `artifact_build` lock, so it inherits that run's id, status record and progress
 * bar and owns none of them (design §4.3). Its whole job is geometry and bytes:
 *
 *   parse (cadjs parseDxf) -> mesh (cadjs buildDxfPreviewMeshData) -> write (writeGlb, render
 *   preset, meshopt-compressed).
 *
 * Contract:
 *   node dxf-artifact.mjs --package-dir <abs> --run-id <id> [--thickness-mm N] [--name N]
 *   stdout is NDJSON progress + exactly one terminal `result` line (implicitjs progressStream).
 *   Anything it wants to SAY goes to stderr, which the parent inherits.
 *
 * `--thickness-mm` is passed in rather than read from the drawing: the producer owns the bake
 * settings, hashes them into the descriptor's `bakeHash`, and both freshness authorities
 * compare against that one number. A builder that picked its own default would put a setting
 * in the payload that nothing could invalidate.
 *
 * `preview.glb` is written temp + rename, and the parent writes the descriptor only after
 * this exits 0 -- so a reader sees a package with no descriptor (needs-build) rather than a
 * descriptor naming a payload that is missing or half-written.
 */

import fs from "node:fs";
import path from "node:path";

import { MeshoptEncoder } from "meshoptimizer";
import { assertWriteLock } from "implicitjs/glb/assertWriteLock.js";
import {
  reportPhase,
  reportResult,
} from "implicitjs/glb/progressStream.js";

import { parseDxf } from "../src/lib/dxf/parseDxf.js";
import { buildDxfPreviewGlb, DXF_PREVIEW_BAKE_FORMAT } from "../src/lib/dxf/previewGlb.js";

const DRAWING_DXF_NAME = "drawing.dxf";
const PREVIEW_GLB_NAME = "preview.glb";

function parseArgs(argv) {
  const args = {};
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (!token.startsWith("--")) continue;
    const next = argv[index + 1];
    if (next === undefined || next.startsWith("--")) {
      args[token.slice(2)] = "true";
    } else {
      args[token.slice(2)] = next;
      index += 1;
    }
  }
  return args;
}

function requireArg(args, name) {
  const value = String(args[name] || "").trim();
  if (!value) {
    throw new Error(`dxf-artifact.mjs requires --${name}`);
  }
  return value;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const packageDir = path.resolve(requireArg(args, "package-dir"));
  const runId = requireArg(args, "run-id");
  const thicknessMm = Number(args["thickness-mm"]);
  if (!Number.isFinite(thicknessMm) || thicknessMm <= 0) {
    throw new Error("dxf-artifact.mjs requires a positive --thickness-mm");
  }
  const name = String(args.name || path.basename(packageDir) || "drawing");

  // Before anything is read or written: prove this process was started by the lock holder.
  assertWriteLock(packageDir, runId);

  reportPhase("parse");
  const dxfPath = path.join(packageDir, DRAWING_DXF_NAME);
  const dxfText = fs.readFileSync(dxfPath, "utf8");
  const dxfData = parseDxf(dxfText, { fileRef: name });

  reportPhase("mesh");
  await MeshoptEncoder.ready;
  const { bytes, stats } = buildDxfPreviewGlb(dxfData, {
    thicknessMm,
    encoder: MeshoptEncoder,
    name,
  });

  reportPhase("write");
  const previewPath = path.join(packageDir, PREVIEW_GLB_NAME);
  const tempPath = `${previewPath}.tmp-${process.pid}`;
  fs.writeFileSync(tempPath, bytes);
  fs.renameSync(tempPath, previewPath);

  reportResult({
    ok: true,
    // Echoed so the parent can assert that ONE run id spanned both runtimes.
    runId,
    preview: PREVIEW_GLB_NAME,
    bakeFormat: DXF_PREVIEW_BAKE_FORMAT,
    thicknessMm,
    bytes: bytes.length,
    triangleCount: stats.triangleCount,
    vertexCount: stats.vertexCount,
  });
}

try {
  await main();
} catch (error) {
  // stderr is the diagnostic channel (inherited by the parent); a non-zero exit is what the
  // parent turns into a failed run, and the descriptor is never written.
  process.stderr.write(`${error?.stack || error}\n`);
  process.exit(1);
}
