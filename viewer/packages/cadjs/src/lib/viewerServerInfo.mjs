import path from "node:path";

import {
  DEFAULT_VIEWER_ROOT_DIR,
  normalizeViewerRootDir,
  resolveViewerRoot,
} from "./cadDirectoryScanner.mjs";

export const VIEWER_SERVER_INFO_SCHEMA_VERSION = 1;
export const VIEWER_SERVER_API_VERSION = 2;
export const VIEWER_SERVER_APP_ID = "cad-viewer";
export const DEFAULT_VIEWER_HOST = "127.0.0.1";
export const DEFAULT_VIEWER_PORT = 4178;

export function normalizeViewerPort(value, fallback = DEFAULT_VIEWER_PORT) {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  if (Number.isInteger(parsed) && parsed > 0 && parsed <= 65535) {
    return parsed;
  }
  return fallback;
}

export function buildViewerServerInfo({
  workspaceRoot,
  rootDir = DEFAULT_VIEWER_ROOT_DIR,
  port = DEFAULT_VIEWER_PORT,
  pid = process.pid,
  host = DEFAULT_VIEWER_HOST,
  backend = "local-fs",
  dynamicRoot = false,
  stepArtifactGenerationAvailable = true,
  viewerVersion = "",
  serverFeatures = [],
} = {}) {
  if (!workspaceRoot) {
    throw new Error("workspaceRoot is required");
  }
  const resolvedWorkspaceRoot = path.resolve(workspaceRoot);
  const rawRootDir = String(rootDir || "").trim();
  const resolvedViewerRoot = rawRootDir
    ? path.isAbsolute(rawRootDir)
      ? {
          dir: path.resolve(rawRootDir),
          rootPath: path.resolve(rawRootDir),
          rootName: path.basename(path.resolve(rawRootDir)),
        }
      : resolveViewerRoot(resolvedWorkspaceRoot, normalizeViewerRootDir(rawRootDir))
    : {
        dir: DEFAULT_VIEWER_ROOT_DIR,
        rootPath: "",
        rootName: "",
      };
  const normalizedPort = normalizeViewerPort(port);
  return {
    schemaVersion: VIEWER_SERVER_INFO_SCHEMA_VERSION,
    serverApiVersion: VIEWER_SERVER_API_VERSION,
    app: VIEWER_SERVER_APP_ID,
    viewerVersion: String(viewerVersion || ""),
    serverFeatures: Array.isArray(serverFeatures)
      ? serverFeatures.map((feature) => String(feature || "").trim()).filter(Boolean)
      : [],
    backend,
    dynamicRoot: Boolean(dynamicRoot),
    workspaceRoot: resolvedWorkspaceRoot,
    rootDir: resolvedViewerRoot.dir,
    rootPath: resolvedViewerRoot.rootPath,
    rootName: resolvedViewerRoot.rootName,
    port: normalizedPort,
    pid: Number.isInteger(pid) ? pid : process.pid,
    stepArtifactGenerationAvailable: stepArtifactGenerationAvailable !== false,
    url: `http://${host}:${normalizedPort}`,
  };
}

export function isViewerServerInfo(value) {
  return Boolean(
    value &&
    typeof value === "object" &&
    value.app === VIEWER_SERVER_APP_ID &&
    typeof value.rootPath === "string" &&
    Number.isInteger(value.port)
  );
}
