// Generation lock — the viewer's read side of the per-model build lock.
//
// A build (cadpy generation_status.track_generation_run) writes a heartbeated lock file beside the
// model's __cadcache__ package dir while it runs. The artifact pull reads it: an ACTIVE lock (a
// live process heartbeating recently) means a build is in flight, so the pull reports `generating`
// and a viewer-triggered build awaits it instead of spawning a duplicate. A stale lock (dead pid or
// no recent heartbeat) is ignored, so a crashed build is transparently taken over.

import fs from "node:fs";
import path from "node:path";

export const GENERATION_LOCK_SUFFIX = ".generation.lock.json";
const DEFAULT_ACTIVE_MAX_AGE_MS = 30_000;
const DEFAULT_AWAIT_TIMEOUT_MS = 180_000;
const DEFAULT_AWAIT_POLL_MS = 400;

// The single per-model lock, beside the package dir: `<dir>/.<name>.generation.lock.json`.
// Mirrors cadpy generation_status.generation_lock_path so writer and reader agree.
export function generationLockPath(packageDir) {
  const dir = String(packageDir || "").trim();
  if (!dir) {
    return "";
  }
  return path.join(path.dirname(dir), `.${path.basename(dir)}${GENERATION_LOCK_SUFFIX}`);
}

export function isGenerationLockPath(filePath) {
  const name = path.basename(String(filePath || ""));
  return name.startsWith(".") && name.endsWith(GENERATION_LOCK_SUFFIX);
}

function processIsAlive(pid) {
  const normalizedPid = Number(pid);
  if (!Number.isInteger(normalizedPid) || normalizedPid <= 0) {
    return false;
  }
  try {
    process.kill(normalizedPid, 0);
    return true;
  } catch (error) {
    return error?.code === "EPERM";
  }
}

function readLockPayload(lockPath) {
  try {
    const payload = JSON.parse(fs.readFileSync(lockPath, "utf8"));
    return payload && typeof payload === "object" ? payload : null;
  } catch {
    return null;
  }
}

// A lock is active iff a live process is still heartbeating it recently. Dead pid or a stale
// heartbeat (build crashed/killed) reads as inactive, so the caller proceeds to build (take-over).
export function generationLockActive(lockPath, { nowMs = Date.now(), maxAgeMs = DEFAULT_ACTIVE_MAX_AGE_MS } = {}) {
  const payload = lockPath ? readLockPayload(lockPath) : null;
  if (!payload || String(payload.status || "").trim().toLowerCase() !== "running") {
    return false;
  }
  if (!processIsAlive(payload.pid)) {
    return false;
  }
  const updatedAtMs = Date.parse(String(payload.updatedAt || payload.startedAt || ""));
  return !Number.isFinite(updatedAtMs) || (nowMs - updatedAtMs) <= maxAgeMs;
}

// Wait until an in-flight build's lock clears (the holder finished or died). Resolves true if the
// lock is no longer active, false on timeout. The caller then re-checks artifact freshness.
export async function awaitGenerationLock(lockPath, {
  timeoutMs = DEFAULT_AWAIT_TIMEOUT_MS,
  pollMs = DEFAULT_AWAIT_POLL_MS,
  now = () => Date.now(),
  sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
} = {}) {
  const deadline = now() + timeoutMs;
  while (generationLockActive(lockPath, { nowMs: now() }) && now() < deadline) {
    await sleep(pollMs);
  }
  return !generationLockActive(lockPath, { nowMs: now() });
}
