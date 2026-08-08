import { loadImplicitCadModule } from "cadjs/implicit/loader";
import { exportImplicitCadModel } from "cadjs/implicit/exportModel";

import { refreshCadCatalog } from "./cadManifestStore.js";

export const IMPLICIT_EXPORT_FORMATS = Object.freeze(["stl", "glb", "3mf"]);
export const DEFAULT_IMPLICIT_EXPORT_RESOLUTION = 96;

function normalizedFileRef(value) {
  return String(value || "").trim().replace(/\\/g, "/").replace(/^\/+/, "");
}

function normalizedFormat(value) {
  const format = String(value || "").trim().toLowerCase().replace(/^\./, "");
  return IMPLICIT_EXPORT_FORMATS.includes(format) ? format : "";
}

function isObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

// Implicit export runs entirely in the browser: we load the .implicit.js model
// module, mesh + serialize it client-side (the implicit-CAD geometry runtime is
// pure JS and already loaded for live preview), and upload the encoded bytes.
// The backend just writes them beside the source and refreshes the catalog, so
// no JS geometry runtime is required server-side.
export async function requestImplicitCadExport({
  file,
  moduleUrl,
  format,
  parameterValues = null,
  animationState = null,
  resolution = DEFAULT_IMPLICIT_EXPORT_RESOLUTION,
} = {}) {
  const fileRef = normalizedFileRef(file);
  const exportFormat = normalizedFormat(format);
  const sourceUrl = String(moduleUrl || "").trim();
  if (!fileRef) {
    throw new Error("Missing implicit CAD file");
  }
  if (!exportFormat) {
    throw new Error(`Unsupported implicit CAD export format: ${format || "(missing)"}`);
  }
  if (!sourceUrl) {
    throw new Error("Missing implicit CAD module URL");
  }

  const model = await loadImplicitCadModule(sourceUrl);
  const exported = exportImplicitCadModel(model, {
    format: exportFormat,
    parameterValues: isObject(parameterValues) ? parameterValues : null,
    animationState: isObject(animationState) ? animationState : null,
    resolution,
  });
  const body = exported.body;
  if (!body || !body.length) {
    throw new Error("Implicit CAD export produced no data");
  }

  const response = await fetch(
    `/__cad/implicit-export?file=${encodeURIComponent(fileRef)}&format=${encodeURIComponent(exportFormat)}`,
    {
      method: "POST",
      headers: {
        "content-type": exported.contentType || "application/octet-stream",
      },
      body,
    }
  );
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok || !payload?.ok) {
    throw new Error(String(payload?.error || `Implicit CAD export failed with HTTP ${response.status}`));
  }
  if (payload.catalog) {
    refreshCadCatalog({ markRefreshing: false }).catch(() => {});
  }
  return payload;
}
