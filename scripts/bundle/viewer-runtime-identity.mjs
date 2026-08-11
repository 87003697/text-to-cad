#!/usr/bin/env node
import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const schema = "cad-viewer.runtime-identity/1";
const artifactRoutes = [
  {
    role: "launcher",
    source: "viewer/scripts/start-agent-viewer.mjs",
    bundle: "scripts/start-agent-viewer.mjs",
  },
  {
    role: "server",
    source: "viewer/src/server/server.mjs",
    bundle: "backend/server.mjs",
  },
  {
    role: "client",
    source: "viewer/src/client/main.jsx",
    bundle: "dist/index.html",
  },
];

async function sha256(filePath) {
  return crypto.createHash("sha256").update(await fs.readFile(filePath)).digest("hex");
}

export async function buildViewerRuntimeIdentity({ repoRoot, runtimeRoot, viewerVersion }) {
  const resolvedRepo = path.resolve(repoRoot);
  const resolvedRuntime = path.resolve(runtimeRoot);
  const version = String(viewerVersion || "").trim();
  if (!version) {
    throw new Error("Viewer runtime identity requires a viewer version");
  }
  const artifacts = [];
  for (const route of artifactRoutes) {
    const bundlePath = `skills/cad-viewer/scripts/viewer/${route.bundle}`;
    artifacts.push({
      role: route.role,
      source: {
        path: route.source,
        sha256: await sha256(path.join(resolvedRepo, route.source)),
      },
      bundle: {
        path: bundlePath,
        sha256: await sha256(path.join(resolvedRuntime, route.bundle)),
      },
    });
  }
  return {
    schema,
    viewer_version: version,
    artifacts,
  };
}

function identityBytes(identity) {
  return `${JSON.stringify(identity, null, 2)}\n`;
}

export async function writeViewerRuntimeIdentity(options) {
  const identity = await buildViewerRuntimeIdentity(options);
  const output = path.join(path.resolve(options.runtimeRoot), "runtime-identity.json");
  await fs.writeFile(output, identityBytes(identity));
  return identity;
}

export async function verifyViewerRuntimeIdentity(options) {
  const expected = identityBytes(await buildViewerRuntimeIdentity(options));
  const output = path.join(path.resolve(options.runtimeRoot), "runtime-identity.json");
  let actual = "";
  try {
    actual = await fs.readFile(output, "utf8");
  } catch {
    throw new Error(`Viewer runtime identity is missing: ${output}`);
  }
  if (actual !== expected) {
    throw new Error(`Viewer runtime identity is stale: ${output}`);
  }
  return JSON.parse(actual);
}

function requiredOption(argv, flag) {
  const index = argv.indexOf(flag);
  const value = index >= 0 ? argv[index + 1] : "";
  if (!value || value.startsWith("--")) {
    throw new Error(`${flag} requires a value`);
  }
  return value;
}

async function main(argv) {
  const command = argv[0];
  if (!new Set(["write", "check"]).has(command)) {
    throw new Error("Usage: viewer-runtime-identity.mjs <write|check> --repo-root <path> --runtime-root <path> --viewer-version <version>");
  }
  const options = {
    repoRoot: requiredOption(argv, "--repo-root"),
    runtimeRoot: requiredOption(argv, "--runtime-root"),
    viewerVersion: requiredOption(argv, "--viewer-version"),
  };
  if (command === "write") {
    await writeViewerRuntimeIdentity(options);
  } else {
    await verifyViewerRuntimeIdentity(options);
  }
}

const isMain = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
  main(process.argv.slice(2)).catch((error) => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  });
}
