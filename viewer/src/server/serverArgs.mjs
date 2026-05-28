import path from "node:path";

function requiredValue(argv, index, flag) {
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

export function parseServerArgs(argv = []) {
  const options = {
    rootDir: "",
    port: null,
    host: "",
    help: false,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--help" || arg === "-h") {
      options.help = true;
      continue;
    }
    if (arg.startsWith("--root-dir=")) {
      options.rootDir = arg.slice("--root-dir=".length);
      continue;
    }
    if (arg === "--root-dir") {
      options.rootDir = requiredValue(argv, index, arg);
      index += 1;
      continue;
    }
    if (arg.startsWith("--port=")) {
      options.port = parsePort(arg.slice("--port=".length), "--port");
      continue;
    }
    if (arg === "--port") {
      options.port = parsePort(requiredValue(argv, index, arg), arg);
      index += 1;
      continue;
    }
    if (arg.startsWith("--host=")) {
      options.host = arg.slice("--host=".length).trim();
      continue;
    }
    if (arg === "--host") {
      options.host = requiredValue(argv, index, arg).trim();
      index += 1;
      continue;
    }
    throw new Error(`Unknown argument: ${arg}`);
  }
  return options;
}

export function serverHelpText() {
  return `Usage: node backend/server.mjs [options]

Options:
  --root-dir <path>  Local Viewer root directory to serve. Overrides VIEWER_LOCAL_* env roots.
  --port <number>    Port to bind. Overrides VIEWER_PORT.
  --host <host>      Host to bind. Overrides VIEWER_HOST.
  -h, --help         Show this help.
`;
}

export function applyServerArgsToEnv({
  argv = [],
  env = process.env,
  cwd = process.cwd(),
} = {}) {
  const args = parseServerArgs(argv);
  const nextEnv = { ...env };
  if (args.rootDir) {
    nextEnv.VIEWER_ASSET_BACKEND = "local-fs";
    nextEnv.VIEWER_LOCAL_WORKSPACE_ROOT = path.resolve(cwd, args.rootDir);
    nextEnv.VIEWER_LOCAL_ROOT_DIR = "";
  }
  if (args.port !== null) {
    nextEnv.VIEWER_PORT = String(args.port);
  }
  if (args.host) {
    nextEnv.VIEWER_HOST = args.host;
  }
  return { args, env: nextEnv };
}
