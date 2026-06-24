// Render-artifact pipeline — a small, shareable seam for (re)generating the cache a viewer entry
// needs in order to render.
//
// Most file types render directly from their committed file (mesh / dxf / urdf / gcode / implicit
// shader): they have no derived artifact and are always READY. A few types derive a generated
// artifact under __cadcache__ (today: STEP -> component-GLB package). This module is type-agnostic:
// all per-type behavior lives behind a RenderArtifactProvider, and the resolver maps the per-type
// freshness onto one tiny state machine — ready | needs-build | generating | error.
//
// The framing: a missing or stale cache is NOT an issue to report, it is a trigger to build and
// show a loading state. The only user-facing failure is ERROR (a fatal, non-buildable problem).

export const ARTIFACT_STATE = Object.freeze({
  READY: "ready", // the render artifact exists and is fresh — render it
  NEEDS_BUILD: "needs-build", // missing or stale — the caller should build it
  GENERATING: "generating", // a build is in flight (client-side, while a build request runs)
  ERROR: "error", // a fatal, non-buildable failure — surface to the user
});

/**
 * @typedef {Object} RenderArtifactProvider
 * @property {string} kind                                    e.g. "step"
 * @property {(entry: any) => boolean} owns                   does this provider own this catalog entry?
 * @property {(args: ArtifactArgs) => ArtifactStatus} status  freshness only — never builds
 * @property {(args: ArtifactArgs) => Promise<ArtifactResult>} build  idempotent (re)build
 *
 * @typedef {{ fileRef: string, entry?: any, force?: boolean, resolvedRoot?: any, catalog?: any }} ArtifactArgs
 * @typedef {{ state: string, ref?: string, reason?: string, error?: string }} ArtifactStatus
 * @typedef {{ ok: boolean, state: string, ref?: string, error?: string }} ArtifactResult
 */

// Generic freshness classifier. A provider validates its artifact and yields {ok, error-code}; a
// code in the provider's buildable set means NEEDS_BUILD (regenerate — not an issue), and any other
// not-ok means a fatal ERROR. This is the generalization of the STEP-specific canBuildStepArtifact.
export function classifyArtifactFreshness({ ok, error } = {}, buildableCodes = new Set()) {
  if (ok !== false) {
    return { state: ARTIFACT_STATE.READY };
  }
  const code = String(error || "").trim();
  const set = buildableCodes instanceof Set ? buildableCodes : new Set(buildableCodes || []);
  return set.has(code)
    ? { state: ARTIFACT_STATE.NEEDS_BUILD, reason: code }
    : { state: ARTIFACT_STATE.ERROR, reason: code, error: code };
}

// Build the resolver over a set of providers. An entry no provider owns renders directly from its
// committed file, so it is always READY with the asset url as its ref.
export function createRenderArtifactPipeline({ providers = [], directRef = () => "" } = {}) {
  const list = (Array.isArray(providers) ? providers : []).filter(Boolean);

  function providerFor(entry) {
    return list.find((provider) => {
      try {
        return Boolean(provider?.owns?.(entry));
      } catch {
        return false;
      }
    }) || null;
  }

  // Freshness only — never triggers a build. Returns { state, ref?, ... }.
  function artifactStatus(args = {}) {
    const provider = providerFor(args.entry);
    if (!provider) {
      return { state: ARTIFACT_STATE.READY, ref: directRef(args.entry) };
    }
    return provider.status(args);
  }

  // (Re)build if the provider owns it; direct entries resolve to READY immediately.
  async function resolveArtifact(args = {}) {
    const provider = providerFor(args.entry);
    if (!provider) {
      return { ok: true, state: ARTIFACT_STATE.READY, ref: directRef(args.entry) };
    }
    return provider.build(args);
  }

  return { providerFor, artifactStatus, resolveArtifact };
}
