#!/usr/bin/env node
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { createLocalAssetBackend } from "./localAssetBackend.mjs";
import {
  buildHostedViewerServerInfo,
} from "./vercelApi.mjs";
import { createVercelBlobAssetBackend } from "./vercelBlobAssetBackend.mjs";
import {
  createCadViewerApiMiddleware,
  createLocalAssetMiddleware,
  serveDistAsset,
} from "./httpHandlers.mjs";
import {
  DEFAULT_VIEWER_PORT,
  buildViewerServerInfo,
  normalizeViewerPort,
} from "cadjs/lib/viewerServerInfo.mjs";
import {
  normalizeViewerDefaultFile,
  normalizeViewerGithubUrl,
} from "cadjs/lib/viewerConfig.mjs";
import { resolveWorkspaceRoot } from "cadjs/lib/pathUtils.mjs";
import {
  normalizeViewerAssetBackend,
  rootDirForAssetBackend,
  vercelBlobConfigFromEnv,
  VIEWER_ASSET_BACKENDS,
} from "./viewerEnv.mjs";
import {
  closeHttpServer,
  normalizeServerLifetimeMs,
  scheduleProcessShutdown,
} from "./serverLifetime.mjs";
import {
  applyServerArgsToEnv,
  serverHelpText,
} from "./serverArgs.mjs";

const serverModuleDir = path.dirname(fileURLToPath(import.meta.url));
const viewerAppRoot = path.basename(path.dirname(serverModuleDir)) === "src"
  ? path.resolve(serverModuleDir, "..", "..")
  : path.resolve(serverModuleDir, "..");
const defaultWorkspaceRoot = path.resolve(viewerAppRoot, "..");
let runtime;
try {
  runtime = applyServerArgsToEnv({ argv: process.argv.slice(2), env: process.env, cwd: process.cwd() });
} catch (error) {
  process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
  process.exit(1);
}
if (runtime.args.help) {
  process.stdout.write(serverHelpText());
  process.exit(0);
}
const runtimeEnv = runtime.env;
const workspaceRoot = resolveWorkspaceRoot({
  env: runtimeEnv,
  cwd: process.cwd(),
  appRoot: viewerAppRoot,
  defaultWorkspaceRoot,
});
const backendKind = normalizeViewerAssetBackend(runtimeEnv.VIEWER_ASSET_BACKEND);
const rootDir = rootDirForAssetBackend(backendKind, runtimeEnv);
const port = normalizeViewerPort(runtimeEnv.VIEWER_PORT, DEFAULT_VIEWER_PORT);
const host = runtimeEnv.VIEWER_HOST || "127.0.0.1";
const serverLifetimeMs = normalizeServerLifetimeMs(runtimeEnv.VIEWER_SERVER_LIFETIME_MS);
const distRoot = path.resolve(viewerAppRoot, "dist");
const backend = backendKind === VIEWER_ASSET_BACKENDS.VERCEL_BLOB
  ? createVercelBlobAssetBackend({
      ...vercelBlobConfigFromEnv(runtimeEnv),
      readOnly: true,
    })
  : createLocalAssetBackend({
      workspaceRoot,
      rootDir,
      defaultFile: normalizeViewerDefaultFile(runtimeEnv.VIEWER_DEFAULT_FILE || ""),
      githubUrl: normalizeViewerGithubUrl(runtimeEnv.VIEWER_GITHUB_URL || ""),
    });
const localAssetBackendEnabled = backend.kind === "local-fs";
const stepArtifactBackendEnabled = localAssetBackendEnabled && typeof backend.generateStepArtifact === "function";

if (localAssetBackendEnabled && typeof backend.refreshCatalog === "function") {
  backend.refreshCatalog({ rootDir });
}

const middlewares = [
  createCadViewerApiMiddleware({
    backend,
    rootDir,
    enableStepArtifactBackend: stepArtifactBackendEnabled,
    claimDisabledStepArtifactRoute: true,
    serverInfo: () => (
      localAssetBackendEnabled
        ? buildViewerServerInfo({
            workspaceRoot,
            rootDir,
            port,
            pid: process.pid,
          })
        : buildHostedViewerServerInfo({ backend, env: runtimeEnv, rootDir })
    ),
  }),
  ...(localAssetBackendEnabled ? [createLocalAssetMiddleware({ backend, rootDir })] : []),
  serveDistAsset({ distRoot }),
];

function runMiddleware(index, req, res) {
  const middleware = middlewares[index];
  if (!middleware) {
    res.statusCode = 404;
    res.end("Not found");
    return;
  }
  middleware(req, res, () => runMiddleware(index + 1, req, res));
}

const server = http.createServer((req, res) => runMiddleware(0, req, res));

server.listen(port, host, () => {
  console.log(`CAD Viewer backend listening on http://${host}:${port}/ (${backend.kind})`);
  if (serverLifetimeMs !== null) {
    scheduleProcessShutdown({
      lifetimeMs: serverLifetimeMs,
      label: "CAD Viewer backend",
      close: () => closeHttpServer(server),
    });
  }
});
