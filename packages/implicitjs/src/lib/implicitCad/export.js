import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

import { normalizeImplicitCadModel } from "./model.js";
import { meshImplicitCadModel } from "./mesh.js";
import { meshToFormat } from "./exporters.js";
import { createImplicitCadColorEvaluator } from "./sdfEvaluator.js";

export const IMPLICIT_CAD_EXPORT_FORMATS = Object.freeze(["glb", "stl", "3mf"]);

function isObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function normalizeFormat(value) {
  const normalized = String(value || "").trim().toLowerCase().replace(/^\./, "");
  if (normalized === "gltf") {
    return "glb";
  }
  if (IMPLICIT_CAD_EXPORT_FORMATS.includes(normalized)) {
    return normalized;
  }
  return "";
}

function formatFromOutputPath(outputPath) {
  return normalizeFormat(path.extname(String(outputPath || "")).slice(1));
}

function rgb01ToHex(rgb) {
  const channel = (value) => {
    const numeric = Number(value);
    const clamped = Math.min(Math.max(Number.isFinite(numeric) ? numeric : 0, 0), 1);
    return Math.round(clamped * 255).toString(16).padStart(2, "0");
  };
  return `#${channel(rgb?.[0])}${channel(rgb?.[1])}${channel(rgb?.[2])}`;
}

function sampleImplicitCadMeshColor(model, mesh, { sampleLimit = 768 } = {}) {
  if (!model?.colorSource) {
    return model?.material?.color;
  }
  try {
    const colorAt = createImplicitCadColorEvaluator(model);
    const positions = mesh.positions || new Float32Array();
    const normals = mesh.normals && mesh.normals.length === positions.length
      ? mesh.normals
      : null;
    const vertexCount = Math.floor(positions.length / 3);
    const stride = Math.max(1, Math.floor(vertexCount / Math.max(sampleLimit, 1)));
    const sum = [0, 0, 0];
    let count = 0;
    for (let vertex = 0; vertex < vertexCount; vertex += stride) {
      const offset = vertex * 3;
      const color = colorAt(
        [positions[offset], positions[offset + 1], positions[offset + 2]],
        normals ? [normals[offset], normals[offset + 1], normals[offset + 2]] : [0, 0, 1]
      );
      if (!color.every((component) => Number.isFinite(component))) {
        continue;
      }
      sum[0] += color[0];
      sum[1] += color[1];
      sum[2] += color[2];
      count += 1;
    }
    return count ? rgb01ToHex(sum.map((component) => component / count)) : model?.material?.color;
  } catch {
    return model?.material?.color;
  }
}

export function defaultImplicitCadExportPath(inputPath, format = "glb") {
  const normalizedFormat = normalizeFormat(format) || "glb";
  const raw = String(inputPath || "").trim();
  const basename = raw
    .replace(/\.implicit\.(?:mjs|js)$/i, "")
    .replace(/\.(?:mjs|js)$/i, "");
  return `${basename}.${normalizedFormat}`;
}

export async function loadImplicitCadModelFromPath(inputPath, {
  params = null,
  parameterValues = null,
  animationState = null,
} = {}) {
  const resolvedInput = path.resolve(String(inputPath || ""));
  const stats = await fs.stat(resolvedInput);
  if (!stats.isFile()) {
    throw new Error(`Implicit CAD input is not a file: ${resolvedInput}`);
  }
  const inputUrl = pathToFileURL(resolvedInput);
  inputUrl.searchParams.set("mtime", String(Number(stats.mtimeMs)));
  const moduleValue = await import(inputUrl.href);
  const defaultModel = normalizeImplicitCadModel(moduleValue, { sourceUrl: inputUrl.href });
  const nextParams = isObject(params) ? params : isObject(parameterValues) ? parameterValues : null;
  if (nextParams || animationState) {
    return defaultModel.definition.buildModel(
      nextParams || defaultModel.defaultParameterValues,
      isObject(animationState) ? animationState : defaultModel.animationState
    );
  }
  return defaultModel;
}

export function exportImplicitCadModel(modelValue, {
  format = "glb",
  resolution = 96,
  maxCells = undefined,
  normalEpsilon = undefined,
  smoothNormals = undefined,
} = {}) {
  const model = normalizeImplicitCadModel(modelValue);
  const outputFormat = normalizeFormat(format);
  const mesh = meshImplicitCadModel(model, {
    resolution,
    maxCells,
    normalEpsilon,
    smoothNormals: smoothNormals ?? outputFormat === "glb",
  });
  if (!mesh.triangleCount) {
    throw new Error("Implicit CAD export produced an empty mesh. Check bounds, parameters, and resolution.");
  }
  const exported = meshToFormat(mesh, format, {
    name: model.name,
    color: sampleImplicitCadMeshColor(model, mesh),
  });
  return {
    ...exported,
    mesh,
    model,
    format: outputFormat,
  };
}

export async function exportImplicitCadFile({
  input,
  output = "",
  format = "",
  params = null,
  parameterValues = null,
  animationState = null,
  resolution = 96,
  maxCells = undefined,
  normalEpsilon = undefined,
  smoothNormals = undefined,
} = {}) {
  const inputPath = path.resolve(String(input || ""));
  const outputFormat = normalizeFormat(format) || formatFromOutputPath(output) || "glb";
  const outputPath = path.resolve(output || defaultImplicitCadExportPath(inputPath, outputFormat));
  const model = await loadImplicitCadModelFromPath(inputPath, {
    params,
    parameterValues,
    animationState,
  });
  const result = exportImplicitCadModel(model, {
    format: outputFormat,
    resolution,
    maxCells,
    normalEpsilon,
    smoothNormals,
  });
  await fs.mkdir(path.dirname(outputPath), { recursive: true });
  await fs.writeFile(outputPath, result.body);
  return {
    ok: true,
    input: inputPath,
    output: outputPath,
    format: result.format,
    contentType: result.contentType,
    bytes: result.body.length,
    triangleCount: result.mesh.triangleCount,
    vertexCount: result.mesh.vertexCount,
    grid: result.mesh.grid,
    model: {
      name: result.model.name,
      bounds: result.model.bounds,
      units: result.model.units,
    },
  };
}
