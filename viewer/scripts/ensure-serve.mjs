#!/usr/bin/env node
import { spawn } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  DEFAULT_VIEWER_ROOT_DIR,
  normalizeViewerRootDir,
  resolveViewerRoot,
} from "cadjs/lib/cadDirectoryScanner.mjs";
import {
  DEFAULT_VIEWER_HOST,
  DEFAULT_VIEWER_PORT,
  isViewerServerInfo,
  normalizeViewerPort,
} from "cadjs/lib/viewerServerInfo.mjs";
import {
  readViewerServerRegistry,
  writeViewerServerRegistry,
} from "cadjs/lib/viewerServerRegistry.mjs";
import {
  encodePathParam,
  pathIsInsideOrEqual,
  resolveWorkspaceRoot as resolveViewerWorkspaceRoot,
  toPosixPath,
} from "cadjs/lib/pathUtils.mjs";

export const DEFAULT_PORT_END = 4198;
export const DEFAULT_ADAPTIVE_PORT_COUNT = 80;
export const DEFAULT_PROBE_TIMEOUT_MS = 200;
export const DEFAULT_START_TIMEOUT_MS = 30_000;
export const DEFAULT_READY_INTERVAL_MS = 100;

const scriptPath = fileURLToPath(import.meta.url);
const viewerAppRoot = path.resolve(path.dirname(scriptPath), "..");
const defaultWorkspaceRoot = path.resolve(viewerAppRoot, "..");
const sourceServerEntryPath = path.join(viewerAppRoot, "src", "server", "server.mjs");
const serverEntryPath = fs.existsSync(sourceServerEntryPath)
  ? sourceServerEntryPath
  : path.join(viewerAppRoot, "backend", "server.mjs");

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function parseRequiredValue(argv, index, flag) {
  const value = argv[index + 1];
  if (!value || value.startsWith("--")) {
    throw new Error(`${flag} requires a value`);
  }
  return value;
}

function parsePort(value, flag) {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  if (!Number.isInteger(parsed) || parsed <= 0 || parsed > 65535) {
    throw new Error(`${flag} must be a TCP port from 1 to 65535`);
  }
  return parsed;
}

export function parseEnsureServeArgs(argv = []) {
  const options = {
    workspaceRoot: "",
    rootDir: "",
    file: "",
    port: null,
    portEnd: null,
    json: false,
    help: false,
  };
  let fileSeen = false;

  function setFile(value) {
    if (fileSeen) {
      throw new Error("--file may only be provided once; run serve:ensure once per file.");
    }
    fileSeen = true;
    options.file = value;
  }

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--json") {
      options.json = true;
      continue;
    }
    if (arg === "--help" || arg === "-h") {
      options.help = true;
      continue;
    }
    if (arg.startsWith("--workspace-root=")) {
      options.workspaceRoot = arg.slice("--workspace-root=".length);
      continue;
    }
    if (arg === "--workspace-root") {
      options.workspaceRoot = parseRequiredValue(argv, index, arg);
      index += 1;
      continue;
    }
    if (arg.startsWith("--root-dir=")) {
      options.rootDir = arg.slice("--root-dir=".length);
      continue;
    }
    if (arg === "--root-dir") {
      options.rootDir = parseRequiredValue(argv, index, arg);
      index += 1;
      continue;
    }
    if (arg.startsWith("--file=")) {
      setFile(arg.slice("--file=".length));
      continue;
    }
    if (arg === "--file") {
      setFile(parseRequiredValue(argv, index, arg));
      index += 1;
      continue;
    }
    if (arg.startsWith("--port=")) {
      options.port = parsePort(arg.slice("--port=".length), "--port");
      continue;
    }
    if (arg === "--port") {
      options.port = parsePort(parseRequiredValue(argv, index, arg), arg);
      index += 1;
      continue;
    }
    if (arg.startsWith("--port-end=")) {
      options.portEnd = parsePort(arg.slice("--port-end=".length), "--port-end");
      continue;
    }
    if (arg === "--port-end") {
      options.portEnd = parsePort(parseRequiredValue(argv, index, arg), arg);
      index += 1;
      continue;
    }
    throw new Error(`Unknown argument: ${arg}`);
  }

  return options;
}

export function resolveWorkspaceRoot({
  workspaceRoot = "",
  env = process.env,
  cwd = process.cwd(),
  appRoot = viewerAppRoot,
} = {}) {
  return resolveViewerWorkspaceRoot({
    workspaceRoot,
    env,
    cwd,
    appRoot,
    defaultWorkspaceRoot,
  });
}

function chooseFileCandidate(rawFile, { workspaceRoot, rootPath, cwd }) {
  const candidates = [];
  if (path.isAbsolute(rawFile)) {
    candidates.push(path.resolve(rawFile));
  } else {
    candidates.push(path.resolve(workspaceRoot, rawFile));
    candidates.push(path.resolve(rootPath, rawFile));
    candidates.push(path.resolve(cwd, rawFile));
  }

  const uniqueCandidates = [...new Set(candidates)];
  const insideCandidates = uniqueCandidates.filter((candidate) => pathIsInsideOrEqual(candidate, rootPath));
  if (!insideCandidates.length) {
    throw new Error(`CAD Viewer file must be inside the scan root: ${rawFile}`);
  }

  return insideCandidates[0];
}

export function resolveEnsureServeRequest({
  options = {},
  env = process.env,
  cwd = process.cwd(),
  appRoot = viewerAppRoot,
} = {}) {
  const workspaceRoot = resolveWorkspaceRoot({
    workspaceRoot: options.workspaceRoot,
    env,
    cwd,
    appRoot,
  });
  const rootDir = normalizeViewerRootDir(
    options.rootDir || env.VIEWER_LOCAL_ROOT_DIR || DEFAULT_VIEWER_ROOT_DIR
  );
  const resolvedRoot = resolveViewerRoot(workspaceRoot, rootDir);
  let fileParam = "";
  if (options.file) {
    const filePath = chooseFileCandidate(options.file, {
      workspaceRoot,
      rootPath: resolvedRoot.rootPath,
      cwd,
    });
    fileParam = toPosixPath(path.relative(resolvedRoot.rootPath, filePath));
  }

  const port = options.port || normalizeViewerPort(env.VIEWER_PORT, DEFAULT_VIEWER_PORT);
  const envPortEnd = env.VIEWER_PORT_END
    ? parsePort(env.VIEWER_PORT_END, "VIEWER_PORT_END")
    : DEFAULT_PORT_END;
  const configuredPortEnd = options.portEnd || envPortEnd;
  const hasExplicitPortEnd = Boolean(options.portEnd || env.VIEWER_PORT_END);
  const portEnd = hasExplicitPortEnd
    ? configuredPortEnd
    : Math.max(configuredPortEnd, port + DEFAULT_ADAPTIVE_PORT_COUNT);
  if (portEnd < port) {
    throw new Error("--port-end must be greater than or equal to --port");
  }

  return {
    workspaceRoot,
    rootDir,
    rootPath: resolvedRoot.rootPath,
    fileParam,
    port,
    portEnd,
  };
}

export function buildViewerUrl(serverInfo, fileParam = "") {
  const url = new URL(serverInfo?.url || `http://${DEFAULT_VIEWER_HOST}:${serverInfo.port}`);
  url.pathname = "/";
  url.search = "";
  if (fileParam) {
    url.search = `?file=${encodePathParam(fileParam)}`;
  }
  return url.href;
}

export function probeViewerServer(port, {
  host = DEFAULT_VIEWER_HOST,
  timeoutMs = DEFAULT_PROBE_TIMEOUT_MS,
} = {}) {
  return new Promise((resolve) => {
    const req = http.get({
      hostname: host,
      port,
      path: "/__cad/server",
      timeout: timeoutMs,
    }, (res) => {
      if (res.statusCode !== 200) {
        res.resume();
        resolve(null);
        return;
      }
      let body = "";
      res.setEncoding("utf8");
      res.on("data", (chunk) => {
        body += chunk;
        if (body.length > 64 * 1024) {
          req.destroy();
          resolve(null);
        }
      });
      res.on("end", () => {
        try {
          const payload = JSON.parse(body);
          resolve(isViewerServerInfo(payload) ? payload : null);
        } catch {
          resolve(null);
        }
      });
    });
    req.on("timeout", () => {
      req.destroy();
      resolve(null);
    });
    req.on("error", () => resolve(null));
  });
}

function normalizedCatalogFileRef(value) {
  return String(value || "").trim().replace(/\\/g, "/").replace(/^\/+/, "");
}

export function catalogHasFile(catalog, fileParam = "") {
  const requestedFile = normalizedCatalogFileRef(fileParam);
  if (!requestedFile) {
    return true;
  }
  return Array.isArray(catalog?.entries) && catalog.entries.some((entry) => (
    normalizedCatalogFileRef(entry?.file) === requestedFile
  ));
}

export function probeViewerCatalog(serverInfo, {
  timeoutMs = DEFAULT_PROBE_TIMEOUT_MS,
} = {}) {
  return new Promise((resolve) => {
    let catalogUrl;
    try {
      catalogUrl = new URL("/__cad/catalog", serverInfo?.url || `http://${DEFAULT_VIEWER_HOST}:${serverInfo?.port}`);
    } catch {
      resolve(null);
      return;
    }
    const req = http.get({
      hostname: catalogUrl.hostname,
      port: catalogUrl.port,
      path: catalogUrl.pathname,
      protocol: catalogUrl.protocol,
      timeout: timeoutMs,
    }, (res) => {
      if (res.statusCode !== 200) {
        res.resume();
        resolve(null);
        return;
      }
      let body = "";
      res.setEncoding("utf8");
      res.on("data", (chunk) => {
        body += chunk;
        if (body.length > 2 * 1024 * 1024) {
          req.destroy();
          resolve(null);
        }
      });
      res.on("end", () => {
        try {
          resolve(JSON.parse(body));
        } catch {
          resolve(null);
        }
      });
    });
    req.on("timeout", () => {
      req.destroy();
      resolve(null);
    });
    req.on("error", () => resolve(null));
  });
}

export async function probeViewerCatalogForFile(serverInfo, fileParam = "", options = {}) {
  if (!normalizedCatalogFileRef(fileParam)) {
    return true;
  }
  return catalogHasFile(await probeViewerCatalog(serverInfo, options), fileParam);
}

export function canBindPort(port, { host = DEFAULT_VIEWER_HOST } = {}) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once("error", (error) => resolve({
      canBind: false,
      errorCode: error?.code || "UNKNOWN",
      message: error?.message || "",
    }));
    server.once("listening", () => {
      server.close(() => resolve({ canBind: true }));
    });
    server.listen(port, host);
  });
}

function normalizeBindResult(result) {
  if (typeof result === "boolean") {
    return { canBind: result, errorCode: "", message: "" };
  }
  return {
    canBind: Boolean(result?.canBind),
    errorCode: typeof result?.errorCode === "string" ? result.errorCode : "",
    message: typeof result?.message === "string" ? result.message : "",
  };
}

function summarizeBindFailures(bindFailures) {
  const byCode = new Map();
  for (const failure of bindFailures) {
    const code = failure.errorCode || "UNKNOWN";
    const current = byCode.get(code) || { count: 0, ports: [] };
    current.count += 1;
    if (current.ports.length < 5) {
      current.ports.push(failure.port);
    }
    byCode.set(code, current);
  }
  return [...byCode.entries()]
    .map(([code, value]) => `${code} on ${value.count} port${value.count === 1 ? "" : "s"} (${value.ports.join(", ")}${value.count > value.ports.length ? ", ..." : ""})`)
    .join("; ");
}

function buildNoAvailablePortError({ port, portEnd, bindFailures }) {
  const deniedFailures = bindFailures.filter((failure) => ["EPERM", "EACCES"].includes(failure.errorCode));
  if (deniedFailures.length) {
    return new Error(
      `No available CAD Viewer port found in ${port}-${portEnd}; local port binding was denied (${summarizeBindFailures(deniedFailures)}). ` +
      "Rerun serve:ensure with local binding permission/escalation, or point --port at an already-running CAD Viewer server."
    );
  }

  if (bindFailures.length) {
    return new Error(
      `No available CAD Viewer port found in ${port}-${portEnd}; bind failures: ${summarizeBindFailures(bindFailures)}`
    );
  }

  return new Error(`No available CAD Viewer port found in ${port}-${portEnd}`);
}

export async function selectViewerServer({
  rootPath,
  fileParam = "",
  port,
  portEnd,
  probeServer = probeViewerServer,
  probeCatalogForFile = probeViewerCatalogForFile,
  canBind = canBindPort,
  registeredServers = readViewerServerRegistry(),
} = {}) {
  const resolvedRootPath = path.resolve(rootPath);
  const normalizedRegisteredServers = (Array.isArray(registeredServers) ? registeredServers : [])
    .filter((server) => isViewerServerInfo(server))
    .sort((a, b) => a.port - b.port);
  const bindFailures = [];
  let registeredMatch = null;

  for (let candidatePort = port; candidatePort <= portEnd; candidatePort += 1) {
    const serverInfo = await probeServer(candidatePort);
    if (isViewerServerInfo(serverInfo)) {
      if (
        path.resolve(serverInfo.rootPath) === resolvedRootPath &&
        await probeCatalogForFile(serverInfo, fileParam)
      ) {
        return {
          action: "reuse",
          port: candidatePort,
          serverInfo,
        };
      }
      continue;
    }

    if (!normalizedCatalogFileRef(fileParam)) {
      registeredMatch ??= normalizedRegisteredServers.find((server) => (
        server.port === candidatePort &&
        path.resolve(server.rootPath) === resolvedRootPath
      )) || null;
    }
  }

  if (registeredMatch) {
    return {
      action: "reuse",
      port: registeredMatch.port,
      serverInfo: registeredMatch,
    };
  }

  for (let candidatePort = port; candidatePort <= portEnd; candidatePort += 1) {
    const bindResult = normalizeBindResult(await canBind(candidatePort));
    if (bindResult.canBind) {
      return {
        action: "start",
        port: candidatePort,
        serverInfo: null,
      };
    }
    if (bindResult.errorCode) {
      bindFailures.push({ port: candidatePort, ...bindResult });
    }
  }
  throw buildNoAvailablePortError({ port, portEnd, bindFailures });
}

export function buildProductionSpawnOptions({ workspaceRoot, rootDir, port, env = process.env } = {}) {
  return {
    command: process.execPath,
    args: [serverEntryPath],
    options: {
      cwd: viewerAppRoot,
      detached: true,
      stdio: "ignore",
      env: {
        ...env,
        VIEWER_ASSET_BACKEND: "local-fs",
        VIEWER_LOCAL_WORKSPACE_ROOT: workspaceRoot,
        VIEWER_LOCAL_ROOT_DIR: rootDir,
        VIEWER_PORT: String(port),
      },
    },
  };
}

export function startProductionServer(request, spawnImpl = spawn) {
  const spawnOptions = buildProductionSpawnOptions(request);
  const child = spawnImpl(spawnOptions.command, spawnOptions.args, spawnOptions.options);
  child.unref?.();
  return child;
}

export async function waitForMatchingServer({
  rootPath,
  fileParam = "",
  port,
  timeoutMs = DEFAULT_START_TIMEOUT_MS,
  intervalMs = DEFAULT_READY_INTERVAL_MS,
  probeServer = probeViewerServer,
  probeCatalogForFile = probeViewerCatalogForFile,
} = {}) {
  const deadline = Date.now() + timeoutMs;
  const resolvedRootPath = path.resolve(rootPath);
  while (Date.now() < deadline) {
    const serverInfo = await probeServer(port);
    if (
      isViewerServerInfo(serverInfo) &&
      path.resolve(serverInfo.rootPath) === resolvedRootPath &&
      await probeCatalogForFile(serverInfo, fileParam)
    ) {
      return serverInfo;
    }
    await sleep(intervalMs);
  }
  throw new Error(`CAD Viewer did not become ready on port ${port}`);
}

export function formatEnsureServeResult({ action, serverInfo, fileParam, json = false } = {}) {
  const url = buildViewerUrl(serverInfo, fileParam);
  if (json) {
    return `${JSON.stringify({
      action,
      url,
      server: serverInfo,
    }, null, 2)}\n`;
  }
  return `${url}\n`;
}

export function helpText() {
  return `Usage: npm run serve:ensure -- [options]

Options:
  --workspace-root <path>  Workspace root to scan. Defaults to INIT_CWD.
  --root-dir <path>        Scan subdirectory inside the workspace root.
  --file <path>            File to open; pass one --file per command.
  --port <number>          First port to probe. Defaults to 4178 or VIEWER_PORT.
  --port-end <number>      Last port to probe. Defaults to 4198, then auto-expands
                           to 80 more ports unless VIEWER_PORT_END is set.
  --json                   Print structured JSON instead of just the Viewer URL.
`;
}

export async function runEnsureServe(argv = process.argv.slice(2), {
  env = process.env,
  cwd = process.cwd(),
  stdout = process.stdout,
  stderr = process.stderr,
  spawnImpl = spawn,
} = {}) {
  const options = parseEnsureServeArgs(argv);
  if (options.help) {
    stdout.write(helpText());
    return 0;
  }

  const request = resolveEnsureServeRequest({ options, env, cwd });
  const selection = await selectViewerServer(request);
  let serverInfo = selection.serverInfo;
  if (selection.action === "start") {
    startProductionServer({
      workspaceRoot: request.workspaceRoot,
      rootDir: request.rootDir,
      port: selection.port,
      env,
    }, spawnImpl);
    serverInfo = await waitForMatchingServer({
      rootPath: request.rootPath,
      fileParam: request.fileParam,
      port: selection.port,
    });
  }
  writeViewerServerRegistry(serverInfo);

  stdout.write(formatEnsureServeResult({
    action: selection.action === "start" ? "started" : "reused",
    serverInfo,
    fileParam: request.fileParam,
    json: options.json,
  }));
  return 0;
}

if (process.argv[1] && path.resolve(process.argv[1]) === scriptPath) {
  runEnsureServe().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 1;
  });
}
