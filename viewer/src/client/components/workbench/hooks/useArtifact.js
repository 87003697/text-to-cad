import { useEffect, useRef, useState } from "react";

import { requestArtifact, requestArtifactStatus } from "../../../workbench/cadManifestStore.js";
import {
  ARTIFACT_PROGRESS_FIRST_POLL_MS,
  ARTIFACT_PROGRESS_POLL_MS,
  normalizeArtifactProgress
} from "../../../workbench/artifactProgress.js";

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
//
// While the build POST is in flight it is polled for PROGRESS. The POST is one long-lived request
// that resolves only when the build finishes, so the position has to come from somewhere else: a
// concurrent GET of the same status route, which reads the sidecar the build writes as it works.
// The poll is strictly read-only — it never triggers a build of its own — so the single POST stays
// the only writer no matter how long it runs.

const READY = { status: "ready", error: "", progress: null };

function isAbortError(error) {
  return error?.name === "AbortError" || /abort/i.test(String(error?.message || ""));
}

export function useArtifact(fileRef, { enabled = true, freshnessKey = "" } = {}) {
  const activeRef = String(enabled ? fileRef || "" : "").trim();
  const key = activeRef ? `${activeRef}:${freshnessKey}` : "";
  const [state, setState] = useState({ key: "", status: "ready", error: "", progress: null });
  const requestSeqRef = useRef(0);

  useEffect(() => {
    if (!activeRef) {
      return undefined;
    }
    const seq = (requestSeqRef.current += 1);
    const controller = new AbortController();
    const isCurrent = () => seq === requestSeqRef.current;
    const settle = (next) => {
      if (isCurrent()) {
        setState({ key, progress: null, ...next });
      }
    };

    // Polls the status route alongside the in-flight build and merges whatever position it
    // reports into the existing `generating` state. Any failure (a poll racing the build's
    // own request, a sidecar not written yet) simply leaves the last known progress in place —
    // this is decoration, and it must never turn into an error the user sees.
    let pollTimer = 0;
    const stopPolling = () => {
      if (pollTimer) {
        window.clearTimeout(pollTimer);
        pollTimer = 0;
      }
    };
    const pollProgress = async () => {
      if (!isCurrent() || controller.signal.aborted) {
        return;
      }
      try {
        const status = await requestArtifactStatus(activeRef, { signal: controller.signal });
        const progress = normalizeArtifactProgress(status?.progress);
        if (progress && isCurrent()) {
          setState((current) => (current.key === key ? { ...current, progress } : current));
        }
      } catch {
        // ignored on purpose — see above
      }
      if (isCurrent() && !controller.signal.aborted) {
        pollTimer = window.setTimeout(pollProgress, ARTIFACT_PROGRESS_POLL_MS);
      }
    };

    (async () => {
      try {
        const status = await requestArtifactStatus(activeRef, { signal: controller.signal });
        if (!isCurrent()) {
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
        // needs-build (or a build already running) -> build, reporting `generating` while the
        // request runs. A build already in flight reports its position on the status response,
        // so seed from it rather than waiting a full poll interval to show anything.
        settle({
          status: "generating",
          error: "",
          progress: normalizeArtifactProgress(status?.progress)
        });
        pollTimer = window.setTimeout(pollProgress, ARTIFACT_PROGRESS_FIRST_POLL_MS);
        const result = await requestArtifact(activeRef, { signal: controller.signal });
        stopPolling();
        if (!isCurrent()) {
          return;
        }
        settle(result?.ok && result.state === "ready"
          ? READY
          : { status: "error", error: String(result?.error || "Failed to generate the render artifact.") });
      } catch (error) {
        stopPolling();
        if (isCurrent() && !isAbortError(error) && !controller.signal.aborted) {
          settle({ status: "error", error: error instanceof Error ? error.message : String(error) });
        }
      }
    })();

    return () => {
      stopPolling();
      controller.abort();
    };
  }, [activeRef, key]);

  // Optimistic-ready until this exact key has settled, so a fresh selection renders without a flash.
  return state.key === key
    ? { status: state.status, error: state.error, progress: state.progress }
    : READY;
}
