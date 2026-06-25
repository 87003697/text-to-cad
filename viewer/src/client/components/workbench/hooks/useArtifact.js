import { useEffect, useRef, useState } from "react";

import { requestArtifact, requestArtifactStatus } from "../../../workbench/cadManifestStore.js";

// useArtifact — the client half of the render-artifact pipeline.
//
// For the selected entry it resolves a single status: `ready` (render it), `generating` (a (re)build
// is running — show a loading state), or `error` (a fatal build/source failure). A missing or stale
// cache is NOT surfaced as an issue: the hook simply triggers a build and reports `generating`.
//
// Optimistic-ready: a freshly-selected entry starts `ready` so a fresh model renders immediately with
// no flash; only when the freshness check comes back `needs-build` do we flip to `generating` (and
// the caller hides the now-known-stale render assets) and POST a build. Direct-render entries
// (`enabled: false`) never hit the network and stay `ready`.

const READY = { status: "ready", error: "" };

function isAbortError(error) {
  return error?.name === "AbortError" || /abort/i.test(String(error?.message || ""));
}

export function useArtifact(fileRef, { enabled = true, freshnessKey = "" } = {}) {
  const activeRef = String(enabled ? fileRef || "" : "").trim();
  const key = activeRef ? `${activeRef}:${freshnessKey}` : "";
  const [state, setState] = useState({ key: "", status: "ready", error: "" });
  const requestSeqRef = useRef(0);

  useEffect(() => {
    if (!activeRef) {
      return undefined;
    }
    const seq = (requestSeqRef.current += 1);
    const controller = new AbortController();
    const settle = (next) => {
      if (seq === requestSeqRef.current) {
        setState({ key, ...next });
      }
    };

    (async () => {
      try {
        const status = await requestArtifactStatus(activeRef, { signal: controller.signal });
        if (seq !== requestSeqRef.current) {
          return;
        }
        const reported = String(status?.state || "ready");
        if (reported === "ready") {
          settle(READY);
          return;
        }
        if (reported === "error") {
          settle({ status: "error", error: String(status?.error || status?.reason || "Render artifact is unavailable.") });
          return;
        }
        // needs-build -> build, reporting `generating` while the request runs.
        settle({ status: "generating", error: "" });
        const result = await requestArtifact(activeRef, { signal: controller.signal });
        if (seq !== requestSeqRef.current) {
          return;
        }
        settle(result?.ok && result.state === "ready"
          ? READY
          : { status: "error", error: String(result?.error || "Failed to generate the render artifact.") });
      } catch (error) {
        if (seq === requestSeqRef.current && !isAbortError(error) && !controller.signal.aborted) {
          settle({ status: "error", error: error instanceof Error ? error.message : String(error) });
        }
      }
    })();

    return () => controller.abort();
  }, [activeRef, key]);

  // Optimistic-ready until this exact key has settled, so a fresh selection renders without a flash.
  return state.key === key ? { status: state.status, error: state.error } : READY;
}
