#!/usr/bin/env node
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { parseServerLifetimeMs } from "../src/server/serverLifetime.mjs";

const scriptPath = fileURLToPath(import.meta.url);
const defaultPackageRoot = path.resolve(path.dirname(scriptPath), "..");
const startModeFlag = "--viewer-start-mode";
const startModes = new Set(["auto", "dev", "serve"]);

function requiredValue(argv, index, flag) {
  const value = argv[index + 1];
  if (!value || value.startsWith("--")) {
    throw new Error(`${flag} requires a value`);
  }
  return value;
}

function normalizeStartMode(value, flag = startModeFlag) {
  const mode = String(value || "").trim();
  if (!startModes.has(mode)) {
    throw new Error(`${flag} must be one of: auto, dev, serve.`);
  }
  return mode;
}

export function parseAgentStartArgs(argv = []) {
  const options = {
    startMode: "auto",
    forwardedArgs: [],
    shutdownAfterMs: null,
  };

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg.startsWith(`${startModeFlag}=`)) {
      options.startMode = normalizeStartMode(arg.slice(startModeFlag.length + 1));
      continue;
    }
    if (arg === startModeFlag) {
      options.startMode = normalizeStartMode(requiredValue(argv, index, arg));
      index += 1;
      continue;
    }
    if (arg.startsWith("--shutdown-after=")) {
      options.shutdownAfterMs = parseServerLifetimeMs(arg.slice("--shutdown-after=".length), "--shutdown-after");
      options.forwardedArgs.push(arg);
      continue;
    }
    if (arg === "--shutdown-after") {
      const value = requiredValue(argv, index, arg);
      options.shutdownAfterMs = parseServerLifetimeMs(value, arg);
      options.forwardedArgs.push(arg, value);
      index += 1;
      continue;
    }
    options.forwardedArgs.push(arg);
  }

  return options;
}

export function stripShutdownAfterArgs(argv = []) {
  const stripped = [];
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg.startsWith("--shutdown-after=")) {
      continue;
    }
    if (arg === "--shutdown-after") {
      index += 1;
      continue;
    }
    stripped.push(arg);
  }
  return stripped;
}

export function isSymlinkPath(filePath) {
  if (!filePath) {
    return false;
  }
  try {
    return fs.lstatSync(filePath).isSymbolicLink();
  } catch {
    return false;
  }
}

export function selectAgentStartMode({
  requestedMode = "auto",
  npmConfigPrefix = "",
  npmPackageJson = "",
} = {}) {
  const mode = normalizeStartMode(requestedMode);
  if (mode !== "auto") {
    return mode;
  }
  const packagePrefix = npmConfigPrefix
    || (npmPackageJson ? path.dirname(npmPackageJson) : "")
    || process.env.npm_config_prefix
    || (process.env.npm_package_json ? path.dirname(process.env.npm_package_json) : "");
  return isSymlinkPath(packagePrefix) ? "dev" : "serve";
}

export function buildAgentStartCommand({
  mode,
  packageRoot = defaultPackageRoot,
  forwardedArgs = [],
  shutdownAfterMs = null,
  env = process.env,
  nodePath = process.execPath,
} = {}) {
  const resolvedPackageRoot = path.resolve(packageRoot);
  if (mode === "dev") {
    const nextEnv = { ...env };
    if (shutdownAfterMs !== null) {
      nextEnv.VIEWER_SERVER_LIFETIME_MS = String(shutdownAfterMs);
    }
    return {
      command: nodePath,
      args: [
        path.join(resolvedPackageRoot, "node_modules", "vite", "bin", "vite.js"),
        "dev",
        ...stripShutdownAfterArgs(forwardedArgs),
      ],
      cwd: resolvedPackageRoot,
      env: nextEnv,
      mode,
    };
  }

  return {
    command: nodePath,
    args: [
      path.join(resolvedPackageRoot, "src", "server", "server.mjs"),
      ...forwardedArgs,
    ],
    cwd: resolvedPackageRoot,
    env: { ...env },
    mode,
  };
}

export function resolveAgentStartCommand({
  argv = process.argv.slice(2),
  env = process.env,
  packageRoot = defaultPackageRoot,
  nodePath = process.execPath,
} = {}) {
  const parsed = parseAgentStartArgs(argv);
  const mode = selectAgentStartMode({
    requestedMode: parsed.startMode,
    npmConfigPrefix: env.npm_config_prefix,
    npmPackageJson: env.npm_package_json,
  });
  return buildAgentStartCommand({
    mode,
    packageRoot,
    forwardedArgs: parsed.forwardedArgs,
    shutdownAfterMs: parsed.shutdownAfterMs,
    env,
    nodePath,
  });
}

export function runAgentStart(options = {}) {
  const command = resolveAgentStartCommand(options);
  const child = spawn(command.command, command.args, {
    cwd: command.cwd,
    env: command.env,
    stdio: "inherit",
  });

  child.on("exit", (code, signal) => {
    if (signal) {
      process.kill(process.pid, signal);
      return;
    }
    process.exit(code ?? 1);
  });

  child.on("error", (error) => {
    console.error(`Failed to start CAD Viewer ${command.mode} server: ${error.message}`);
    process.exit(1);
  });

  return child;
}

const isMain = process.argv[1] && path.resolve(process.argv[1]) === scriptPath;
if (isMain) {
  try {
    runAgentStart();
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exit(1);
  }
}
