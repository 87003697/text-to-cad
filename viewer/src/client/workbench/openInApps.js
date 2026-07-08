// "Open in <CAD app>" client helpers: list the server-detected desktop CAD apps
// (GET /__cad/integrations) and hand a STEP entry to one (POST /__cad/open-in).
// The server owns the app allowlist and launch recipes; the client only ever
// names an app id.

export function normalizedOpenInTargets(payload) {
  const targets = Array.isArray(payload?.targets) ? payload.targets : [];
  return targets
    .map((target) => ({
      id: String(target?.id || "").trim(),
      name: String(target?.name || "").trim(),
      installed: target?.installed === true,
    }))
    .filter((target) => target.id && target.name);
}

export async function fetchOpenInTargets({ signal } = {}) {
  const response = await fetch("/__cad/integrations", { cache: "no-store", signal });
  if (!response.ok) {
    return [];
  }
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    return [];
  }
  return normalizedOpenInTargets(payload);
}

export function openInBusyKey(fileRef, appId) {
  const ref = String(fileRef || "").trim();
  const app = String(appId || "").trim();
  return ref && app ? `${ref}:open-in:${app}` : "";
}

export function openInRequestUrl(fileRef, appId) {
  return `/__cad/open-in?file=${encodeURIComponent(String(fileRef || ""))}&app=${encodeURIComponent(String(appId || ""))}`;
}

// Resolves to the server payload ({ ok, appId, appName, filename, path,
// materialized }); throws with the server's error message on failure.
export async function requestOpenInApp({ file, app } = {}) {
  const fileRef = String(file || "").trim().replace(/\\/g, "/");
  const appId = String(app || "").trim();
  if (!fileRef) {
    throw new Error("Missing STEP file");
  }
  if (!appId) {
    throw new Error("Missing Open-in app");
  }
  const response = await fetch(openInRequestUrl(fileRef, appId), {
    method: "POST",
    cache: "no-store",
  });
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok || !payload?.ok) {
    throw new Error(String(payload?.error || `Open in app failed with HTTP ${response.status}`));
  }
  return payload;
}
