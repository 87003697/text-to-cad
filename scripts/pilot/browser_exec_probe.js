#!/usr/bin/env node
"use strict";

const { spawn } = require("node:child_process");
const { writeSync } = require("node:fs");

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
let timer;

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

function finish(result) {
  if (finished)
    return;
  finished = true;
  clearTimeout(timer);
  if (result !== "passed")
    stopChild();
  process.stdout.write(`${result}\n`, () =>
    process.exit(result === "passed" ? 0 : 2));
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
  writeSync(1, "spawn-event\n");
  process.exit(2);
}

timer = setTimeout(() => finish("timeout"), TIMEOUT_MILLISECONDS);

child.once("error", () => finish("spawn-event"));
child.stdout.on("data", (chunk) => {
  const next = appendBounded(stdout, chunk);
  if (next === null)
    finish("output-shape");
  else
    stdout = next;
});
child.stderr.on("data", (chunk) => {
  const next = appendBounded(stderr, chunk);
  if (next === null)
    finish("output-shape");
  else
    stderr = next;
});
child.once("close", (code, childSignal) => {
  if (code !== 0 || childSignal !== null) {
    finish("nonzero-exit");
    return;
  }
  finish(
    stderr.length === 0 && VERSION_OUTPUT.test(stdout.toString("utf8"))
      ? "passed"
      : "output-shape",
  );
});

for (const name of ["SIGINT", "SIGTERM"])
  process.once(name, () => finish("timeout"));
