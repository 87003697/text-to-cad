import { refreshCadCatalog } from "./cadManifestStore.js";

// "Export model" formats for a STEP/assembly entry, in dropdown/menu order.
export const STEP_EXPORT_FORMATS = Object.freeze(["step", "3mf", "stl", "glb"]);

const STEP_EXPORT_FORMAT_LABELS = Object.freeze({
  step: "STEP",
  "3mf": "3MF",
  stl: "STL",
  glb: "GLB",
});

export function stepExportFormatLabel(format) {
  const normalized = String(format || "").trim().toLowerCase();
  return STEP_EXPORT_FORMAT_LABELS[normalized] || normalized.toUpperCase();
}

// An imported model's STEP file is its own source (no `.step.py` generator), so exporting it
// to STEP is just downloading the original. Generated models carry a `source` generator.
export function isImportedStepEntry(entry) {
  return !(entry?.source && entry.source.sourcePath);
}

// Menu label for one export format. For an imported entry, "STEP" reads as "Download STEP"
// (it's the original file); every other case is "Export to <FORMAT>".
export function stepExportItemLabel(format, { imported = false } = {}) {
  const normalized = String(format || "").trim().toLowerCase();
  if (normalized === "step" && imported) {
    return "Download STEP";
  }
  return `Export to ${stepExportFormatLabel(normalized)}`;
}

function normalizedFileRef(value) {
  // Keep a leading "/" intact: catalog `entry.file` refs are absolute in dynamic-root mode,
  // and the server resolves absolute refs directly (stripping the slash would turn an
  // absolute path into a bogus relative one → "STEP file not found"). Mirrors
  // downloadUrlForFileAsset, which sends the ref as-is.
  return String(value || "").trim().replace(/\\/g, "/");
}

function normalizedFormat(value) {
  const format = String(value || "").trim().toLowerCase().replace(/^\./, "");
  return STEP_EXPORT_FORMATS.includes(format) ? format : "";
}

// Request a server-side export of one STEP/assembly model to `format`, written to a path the
// user picks via the OS-native save dialog. Resolves to one of:
//   { cancelled: true }                       — user dismissed the save dialog (not an error)
//   { ok, path, filename, format }            — written directly to the chosen path
//   { ok, fallback, downloadUrl, filename }   — no native dialog; fetch the file to download it
// Throws on a genuine failure.
export async function requestStepExport({ file, format } = {}) {
  const fileRef = normalizedFileRef(file);
  const exportFormat = normalizedFormat(format);
  if (!fileRef) {
    throw new Error("Missing STEP file");
  }
  if (!exportFormat) {
    throw new Error(`Unsupported export format: ${format || "(missing)"}`);
  }
  const response = await fetch(
    `/__cad/step-export?file=${encodeURIComponent(fileRef)}&format=${encodeURIComponent(exportFormat)}`,
    {
      method: "POST",
      headers: {
        "content-type": "application/json",
      },
      body: JSON.stringify({ file: fileRef, format: exportFormat }),
    }
  );
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (payload?.cancelled) {
    return { cancelled: true };
  }
  if (!response.ok || !payload?.ok) {
    throw new Error(String(payload?.error || `Export failed with HTTP ${response.status}`));
  }
  if (payload.catalogChanged || payload.fallback) {
    // The export landed inside the viewer root, so the catalog gained/updated an entry —
    // refresh so the new file shows up in the tree without a manual rescan.
    refreshCadCatalog({ markRefreshing: false }).catch(() => {});
  }
  return payload;
}
