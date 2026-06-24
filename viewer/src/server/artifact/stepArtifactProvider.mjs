// StepArtifactProvider — the one concrete RenderArtifactProvider today.
//
// A STEP model (Python gen_step generator OR imported .step/.stp) renders from a component-GLB
// package under <folder>/__cadcache__/models/<name>.step/. This provider is a thin wrapper: it adds
// NO build logic, it just maps the existing STEP freshness + build closures onto the unified
// render-artifact state machine. The freshness/build internals stay where they are
// (readStepSourceStatus / generateStepArtifact / the cadpy step_artifact CLI).
//
// Build coordination: a build writes a heartbeated lock beside the package dir (cadpy
// generation_status). `lockActive` lets `status()` report GENERATING while any build is in flight,
// and `build()` await an in-flight build instead of spawning a duplicate — taking over only if the
// holder died (stale lock). This replaces the old standalone /__cad/generation-status scan.
//
// Dependency-injected so it can be unit-tested and so the backend supplies its own closures.

import { ARTIFACT_STATE, classifyArtifactFreshness } from "./renderArtifact.mjs";
import { BUILDABLE_STEP_ARTIFACT_CODES } from "../step/stepArtifactCompiler.mjs";

/**
 * @param {Object} deps
 * @param {(entry: any) => boolean} deps.ownsEntry  claim STEP-source entries
 * @param {(args: any) => any} deps.readStatus      backend readStepSourceStatus(ForFile) — freshness
 * @param {(args: any) => Promise<any>} deps.build   backend generateStepArtifact — the (re)build
 * @param {(entry: any, status?: any) => string} [deps.artifactRef]  render-artifact url for an entry
 * @param {(args: any) => boolean} [deps.lockActive]  is a build in flight for this model's package?
 * @param {(args: any) => Promise<boolean>} [deps.awaitLock]  wait for an in-flight build to clear
 */
export function createStepArtifactProvider({ ownsEntry, readStatus, build, artifactRef, lockActive, awaitLock } = {}) {
  if (typeof ownsEntry !== "function" || typeof readStatus !== "function" || typeof build !== "function") {
    throw new TypeError("createStepArtifactProvider requires ownsEntry, readStatus, build");
  }
  const refFor = typeof artifactRef === "function" ? artifactRef : () => "";
  const buildInFlight = typeof lockActive === "function" ? lockActive : () => false;

  // Freshness only — never builds, ignores any in-flight lock. Maps STEP validation onto
  // ready | needs-build | error.
  function freshness({ fileRef, entry, resolvedRoot, catalog } = {}) {
    let status;
    try {
      status = readStatus({ fileRef, resolvedRoot, catalog });
    } catch (error) {
      // The source can't even be resolved (e.g. outside root) — a fatal, non-buildable error.
      return { state: ARTIFACT_STATE.ERROR, error: error instanceof Error ? error.message : String(error) };
    }
    const artifact = status?.artifact || null;
    const classified = classifyArtifactFreshness(
      { ok: artifact ? artifact.ok !== false : true, error: artifact?.error },
      BUILDABLE_STEP_ARTIFACT_CODES,
    );
    return {
      ...classified,
      ref: refFor(entry, status),
      ...(classified.state === ARTIFACT_STATE.ERROR && artifact?.message ? { error: artifact.message } : {}),
    };
  }

  return {
    kind: "step",

    owns(entry) {
      return Boolean(ownsEntry(entry));
    },

    // A build already in flight (this viewer's or an out-of-band CLI/process) takes precedence:
    // report GENERATING so the caller shows a loading state and awaits it rather than re-reading a
    // mid-rebuild cache.
    status(args = {}) {
      if (buildInFlight(args)) {
        return { state: ARTIFACT_STATE.GENERATING, ref: refFor(args.entry) };
      }
      return freshness(args);
    },

    // Idempotent (re)build. If a build is already running for this model, wait for it instead of
    // spawning a duplicate; if it finished fresh, we're done. If its holder died mid-build the lock
    // goes stale, `awaitLock` returns, freshness is still not ready, and we fall through to build
    // (take-over). `force` always rebuilds.
    async build(args = {}) {
      const { entry, force = false } = args;
      if (!force && typeof awaitLock === "function" && buildInFlight(args)) {
        await awaitLock(args);
        const after = freshness(args);
        if (after.state === ARTIFACT_STATE.READY) {
          return { ok: true, state: ARTIFACT_STATE.READY, ref: after.ref };
        }
      }
      const result = await build({ fileRef: args.fileRef, force, resolvedRoot: args.resolvedRoot, catalog: args.catalog });
      if (result?.ok) {
        return { ok: true, state: ARTIFACT_STATE.READY, ref: refFor(entry) };
      }
      return {
        ok: false,
        state: ARTIFACT_STATE.ERROR,
        error: result?.error || "STEP render artifact build failed",
      };
    },
  };
}
