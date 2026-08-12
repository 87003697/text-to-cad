#!/usr/bin/env node
"use strict";

const { spawn } = require("node:child_process");

const EXPECTED_EXECUTABLE =
  "/tmp/provider-free-playwright/attested/" +
  "chrome-headless-shell-linux64/chrome-headless-shell";
const MAX_OUTPUT_BYTES = 128;
const TIMEOUT_MILLISECONDS = 4000;
const VERSION_OUTPUT =
  /^(?:Google Chrome for Testing|Chromium|Chrome|HeadlessChrome) [0-9]+(?:\.[0-9]+){3}\n$/;

const mode = process.argv[2];
if (
  process.argv.length !== 4 ||
  !["attached", "detached"].includes(mode) ||
  process.argv[3] !== EXPECTED_EXECUTABLE
) {
  process.exit(2);
}

let child;
let finished = false;
let stdout = Buffer.alloc(0);
let stderr = Buffer.alloc(0);

function stopChild() {
  if (!child || !child.pid)
    return;
  if (mode === "attached") {
    try {
      child.kill("SIGKILL");
    } catch (_ignored) {
      // The child already exited.
    }
    return;
  }
  try {
    process.kill(-child.pid, "SIGKILL");
  } catch (_error) {
    try {
      child.kill("SIGKILL");
    } catch (_ignored) {
      // The child already exited.
    }
  }
}

function finish(ok) {
  if (finished)
    return;
  finished = true;
  clearTimeout(timer);
  if (!ok)
    stopChild();
  process.exit(ok ? 0 : 2);
}

function appendBounded(current, chunk) {
  if (!Buffer.isBuffer(chunk) || current.length + chunk.length > MAX_OUTPUT_BYTES)
    return null;
  return Buffer.concat([current, chunk]);
}

try {
  child = spawn(EXPECTED_EXECUTABLE, ["--version"], {
    cwd: process.cwd(),
    detached: mode === "detached",
    env: {
      HOME: "/nonexistent",
      LANG: "C.UTF-8",
      PATH: "/usr/bin:/bin",
    },
    shell: false,
    stdio: ["ignore", "pipe", "pipe", "pipe", "pipe"],
  });
} catch (_error) {
  process.exit(2);
}

const timer = setTimeout(() => finish(false), TIMEOUT_MILLISECONDS);

child.once("error", () => finish(false));
child.stdout.on("data", (chunk) => {
  const next = appendBounded(stdout, chunk);
  if (next === null)
    finish(false);
  else
    stdout = next;
});
child.stderr.on("data", (chunk) => {
  const next = appendBounded(stderr, chunk);
  if (next === null)
    finish(false);
  else
    stderr = next;
});
child.once("close", (code, signal) => {
  finish(
    code === 0 &&
      signal === null &&
      stderr.length === 0 &&
      VERSION_OUTPUT.test(stdout.toString("utf8")),
  );
});
