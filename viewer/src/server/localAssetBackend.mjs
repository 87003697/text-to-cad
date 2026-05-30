import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";

import {
  CAD_CATALOG_SCHEMA_VERSION,
  catalogFileRefForPath,
  isServedCadAsset,
  readStepSourceStatus,
  scanCadDirectory,
  scanCadFile,
  sortCatalogEntries,
} from "cadjs/lib/cadDirectoryScanner.mjs";
import {
  generationStatusDir as resolveGenerationStatusDir,
  readGenerationStatus,
} from "cadjs/lib/generationStatus.mjs";
import { pathIsInside } from "cadjs/lib/pathUtils.mjs";
import { ensureStepTopologyArtifact } from "cadjs/lib/step/stepArtifactCompiler.mjs";
import {
  cadPythonEnv,
  cadPythonExecutable,
} from "cadjs/lib/step/pythonStepArtifact.mjs";
import { readTextToCadStepMetadataFile } from "cadjs/lib/step/stepMetadata.mjs";

function toPosixPath(value) {
  return String(value || "").split(path.sep).join("/");
}

function absoluteFileRef(filePath) {
  return toPosixPath(path.resolve(filePath));
}

function relativeFileRef(rootPath, filePath) {
  return toPosixPath(path.relative(path.resolve(rootPath), path.resolve(filePath)));
}

function pathIsInsideOrEqual(childPath, parentPath) {
  const relativePath = path.relative(path.resolve(parentPath), path.resolve(childPath));
  return relativePath === "" || (
    relativePath !== ".." &&
    !relativePath.startsWith(`..${path.sep}`) &&
    !path.isAbsolute(relativePath)
  );
}

function normalizedFileRef(value) {
  const raw = String(value || "").trim().replace(/\\/g, "/");
  if (!raw) {
    return "";
  }
  if (raw.includes("\0")) {
    throw new Error("File path contains an invalid null byte");
  }
  return path.isAbsolute(raw) ? absoluteFileRef(raw) : raw.replace(/^\/+/, "");
}

function normalizedRootDir(value, baseRoot) {
  const raw = String(value || "").trim();
  if (!raw) {
    return "";
  }
  if (raw.includes("\0")) {
    throw new Error("CAD Viewer directory contains an invalid null byte");
  }
  return path.isAbsolute(raw)
    ? path.resolve(raw)
    : path.resolve(baseRoot, raw);
}

function requireDirectory(rootPath) {
  let stats = null;
  try {
    stats = fs.statSync(rootPath);
  } catch {
    throw new Error(`CAD Viewer directory not found: ${rootPath}`);
  }
  if (!stats.isDirectory()) {
    throw new Error(`CAD Viewer directory is not a directory: ${rootPath}`);
  }
}

function catalogEntryForFileRef(catalog, fileRef) {
  const normalized = normalizedFileRef(fileRef);
  if (!normalized || !Array.isArray(catalog?.entries)) {
    return null;
  }
  return catalog.entries.find((entry) => (
    normalizedFileRef(entry?.file) === normalized ||
    normalizedFileRef(entry?.rootRelativeFile) === normalized
  )) || null;
}

function ensurePathInsideRoot(filePath, resolvedRoot) {
  if (!(filePath === resolvedRoot.rootPath || pathIsInside(filePath, resolvedRoot.rootPath))) {
    throw new Error("Requested file is outside the active CAD Viewer root");
  }
}

function normalizedFileAssetKind(value) {
  const asset = String(value || "output").trim().toLowerCase();
  if (asset === "asset") {
    return "artifact";
  }
  if (asset === "output" || asset === "source" || asset === "artifact") {
    return asset;
  }
  throw new Error(`Unsupported file asset: ${asset || "(missing)"}`);
}

function fileHasGenStep(filePath) {
  try {
    return /\bgen_step\s*\(/.test(fs.readFileSync(filePath, "utf-8"));
  } catch {
    return false;
  }
}

function sameStemPythonGeneratorPath(stepPath) {
  const extension = path.extname(stepPath).toLowerCase();
  if (extension !== ".step" && extension !== ".stp") {
    return "";
  }
  const candidate = path.join(path.dirname(stepPath), `${path.basename(stepPath, extension)}.py`);
  return fileHasGenStep(candidate) ? candidate : "";
}

function stepArtifactGenerationError(result) {
  const directError = String(result?.error || "").trim();
  if (directError) {
    return directError;
  }
  const validationError = result?.validation?.error;
  const validationMessage = String(validationError?.message || "").trim();
  if (validationMessage) {
    return validationMessage;
  }
  const reason = String(result?.reason || "").trim();
  if (reason) {
    return `STEP artifact was not generated: ${reason}`;
  }
  return "STEP artifact generation failed.";
}

function appendOption(args, name, value) {
  if (value === null || value === undefined || value === "") {
    return;
  }
  args.push(name, String(value));
}

function appendRepeatedOption(args, name, values) {
  if (!Array.isArray(values)) {
    return;
  }
  for (const value of values) {
    const normalized = String(value || "").trim();
    if (normalized) {
      args.push(name, normalized);
    }
  }
}

export function runCadpyInterference({
  repoRoot,
  stepPath,
  options = {},
} = {}) {
  const resolvedRepoRoot = path.resolve(repoRoot || "");
  const resolvedStepPath = path.resolve(stepPath || "");
  const args = [
    "-m",
    "cadpy.interference",
    resolvedStepPath,
    "--format",
    "json",
  ];
  appendOption(args, "--clearance", options.clearanceMm);
  appendOption(args, "--contact-tolerance", options.contactToleranceMm);
  appendOption(args, "--collision-volume-tolerance", options.collisionVolumeToleranceMm3);
  appendOption(args, "--max-pairs", options.maxPairs);
  appendOption(args, "--time-budget-ms", options.timeBudgetMs);
  appendOption(args, "--body-depth", options.bodyDepth);
  appendRepeatedOption(args, "--collapse", options.collapse);
  appendRepeatedOption(args, "--exclude", options.exclude);
  appendRepeatedOption(args, "--set-a", options.setA);
  appendRepeatedOption(args, "--set-b", options.setB);
  appendRepeatedOption(args, "--pair", options.pairs);
  appendRepeatedOption(args, "--allow-pair", options.allowPairs);
  if (options.includeContact !== false) {
    args.push("--include-contact");
  }
  if (options.includeClearance === true) {
    args.push("--include-clearance");
  }
  if (options.includeSeparated === true) {
    args.push("--include-separated");
  }
  if (options.includeAllowed === true) {
    args.push("--include-allowed");
  }
  if (options.listBodies === true) {
    args.push("--list-bodies");
  }
  if (options.noCache === true) {
    args.push("--no-cache");
  }

  return new Promise((resolve) => {
    const child = spawn(cadPythonExecutable(resolvedRepoRoot), args, {
      cwd: resolvedRepoRoot,
      env: cadPythonEnv(resolvedRepoRoot),
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    child.on("error", (error) => {
      resolve({
        ok: false,
        stepPath: resolvedStepPath,
        error: error instanceof Error ? error.message : String(error),
      });
    });
    child.on("close", (code) => {
      const output = stdout.trim();
      const lastJsonLine = output.split(/\r?\n/).reverse().find((line) => line.trim().startsWith("{"));
      if (code === 0 && lastJsonLine) {
        try {
          resolve({
            ok: true,
            stepPath: resolvedStepPath,
            result: JSON.parse(lastJsonLine),
          });
          return;
        } catch {
          // Fall through to the structured failure below.
        }
      }
      resolve({
        ok: false,
        stepPath: resolvedStepPath,
        exitCode: code,
        error: (stderr || stdout || `Collision detection exited with code ${code}`).trim(),
      });
    });
  });
}

function entryIsPythonBackedStep(entry) {
  const artifactSourceKind = String(entry?.artifact?.sourceKind || "").trim().toLowerCase();
  if (artifactSourceKind === "python") {
    return true;
  }
  const sourceKind = String(entry?.sourceKind || entry?.stepSourceKind || "").trim().toLowerCase();
  if (sourceKind === "python") {
    return true;
  }
  const sourcePath = String(entry?.source?.sourcePath || entry?.source?.file || "").trim().toLowerCase();
  return sourcePath.endsWith(".py");
}

function stepFileHasAvailablePythonSourceMetadata(stepPath, repoRoot) {
  try {
    const metadata = readTextToCadStepMetadataFile(stepPath);
    const sourcePath = String(metadata?.sourcePath || "").trim();
    if (!sourcePath || !sourcePath.toLowerCase().endsWith(".py")) {
      return false;
    }
    const resolvedRepoRoot = path.resolve(repoRoot);
    const candidatePath = path.isAbsolute(sourcePath)
      ? path.resolve(sourcePath)
      : path.resolve(resolvedRepoRoot, sourcePath);
    if (!(candidatePath === resolvedRepoRoot || pathIsInside(candidatePath, resolvedRepoRoot))) {
      return false;
    }
    return fileHasGenStep(candidatePath);
  } catch {
    return false;
  }
}

function contentTypeForPath(filePath) {
  const extension = path.extname(filePath).toLowerCase();
  if (extension === ".js" || extension === ".mjs") {
    return "text/javascript; charset=utf-8";
  }
  if (extension === ".json") {
    return "application/json; charset=utf-8";
  }
  if (extension === ".wasm") {
    return "application/wasm";
  }
  if (extension === ".glb") {
    return "model/gltf-binary";
  }
  if (extension === ".stl") {
    return "model/stl";
  }
  if (extension === ".3mf") {
    return "model/3mf";
  }
  if (extension === ".step" || extension === ".stp") {
    return "application/step";
  }
  if (extension === ".dxf") {
    return "application/dxf";
  }
  if (extension === ".gcode" || extension === ".py") {
    return "text/plain; charset=utf-8";
  }
  if (extension === ".urdf" || extension === ".srdf" || extension === ".sdf") {
    return "application/xml; charset=utf-8";
  }
  if (extension === ".svg") {
    return "image/svg+xml";
  }
  if (extension === ".png") {
    return "image/png";
  }
  return "application/octet-stream";
}

function defaultSourceFileOpener(filePath) {
  let command = "";
  let args = [];
  if (process.platform === "darwin") {
    command = "open";
    args = ["-R", filePath];
  } else if (process.platform === "win32") {
    command = "explorer.exe";
    args = [`/select,${filePath}`];
  } else {
    command = "xdg-open";
    args = [path.dirname(filePath)];
  }
  const child = spawn(command, args, {
    detached: true,
    stdio: "ignore",
  });
  child.unref();
  return {
    command,
  };
}

function emptyCatalog() {
  return {
    schemaVersion: CAD_CATALOG_SCHEMA_VERSION,
    entries: [],
  };
}

function normalizeCatalog(catalog) {
  return {
    schemaVersion: CAD_CATALOG_SCHEMA_VERSION,
    entries: Array.isArray(catalog?.entries) ? catalog.entries : [],
  };
}

function queryValueFromAssetUrl(rawUrl, name) {
  try {
    return new URL(String(rawUrl || ""), "http://cad.local").searchParams.get(name) || "";
  } catch {
    return "";
  }
}

function assetPathFromCatalogUrl(scanRepoRoot, rawUrl) {
  const text = String(rawUrl || "").trim();
  if (!text) {
    return "";
  }
  try {
    const url = new URL(text, "http://cad.local");
    const explicitFile = url.searchParams.get("file");
    if (explicitFile) {
      return path.resolve(explicitFile);
    }
    return path.resolve(scanRepoRoot, decodeURIComponent(url.pathname).replace(/^\/+/, ""));
  } catch {
    return path.resolve(scanRepoRoot, text.replace(/[?#].*$/, "").replace(/^\/+/, ""));
  }
}

function localAssetUrlForPath(filePath, rawUrl = "", { rootDir = "" } = {}) {
  const url = new URL("/__cad/asset", "http://cad.local");
  url.searchParams.set("file", absoluteFileRef(filePath));
  const normalizedRootDir = String(rootDir || "").trim();
  if (normalizedRootDir) {
    url.searchParams.set("dir", normalizedRootDir);
  }
  const version = queryValueFromAssetUrl(rawUrl, "v");
  if (version) {
    url.searchParams.set("v", version);
  }
  return `${url.pathname}${url.search}`;
}

function absolutePathFromCatalogValue(scanRepoRoot, value) {
  const text = String(value || "").trim();
  if (!text) {
    return "";
  }
  if (path.isAbsolute(text)) {
    return path.resolve(text);
  }
  return path.resolve(scanRepoRoot, text);
}

function absolutizeArtifact(artifact, scanRepoRoot) {
  if (!artifact || typeof artifact !== "object") {
    return artifact;
  }
  const next = { ...artifact };
  for (const key of ["stepPath", "glbPath", "sourcePath", "cadPath"]) {
    if (next[key]) {
      next[key] = absoluteFileRef(absolutePathFromCatalogValue(scanRepoRoot, next[key]));
    }
  }
  return next;
}

function absolutizeSource(source, scanRepoRoot) {
  if (!source || typeof source !== "object") {
    return source;
  }
  const next = { ...source };
  for (const key of ["file", "path", "sourcePath"]) {
    if (next[key]) {
      next[key] = absoluteFileRef(absolutePathFromCatalogValue(scanRepoRoot, next[key]));
    }
  }
  return next;
}

function absolutizeSourceStatus(sourceStatus, scanRepoRoot) {
  if (!sourceStatus || typeof sourceStatus !== "object") {
    return sourceStatus;
  }
  const next = { ...sourceStatus };
  for (const key of ["sourcePath", "stepPath", "glbPath"]) {
    if (next[key]) {
      next[key] = absoluteFileRef(absolutePathFromCatalogValue(scanRepoRoot, next[key]));
    }
  }
  return next;
}

function absolutizeCatalogEntry(entry, { rootPath, scanRepoRoot, rootDir = "" }) {
  if (!entry || typeof entry !== "object") {
    return entry;
  }
  const outputPath = path.resolve(rootPath, String(entry.file || ""));
  const next = {
    ...entry,
    file: absoluteFileRef(outputPath),
    rootRelativeFile: relativeFileRef(rootPath, outputPath),
  };

  if (entry.url) {
    const assetPath = assetPathFromCatalogUrl(scanRepoRoot, entry.url);
    next.url = localAssetUrlForPath(assetPath, entry.url, { rootDir });
    next.assetFile = absoluteFileRef(assetPath);
  }
  if (entry.moduleUrl) {
    const modulePath = assetPathFromCatalogUrl(scanRepoRoot, entry.moduleUrl);
    next.moduleUrl = localAssetUrlForPath(modulePath, entry.moduleUrl, { rootDir });
    next.moduleFile = absoluteFileRef(modulePath);
  }
  if (entry.source) {
    next.source = absolutizeSource(entry.source, scanRepoRoot);
  }
  if (entry.sourceStatus) {
    next.sourceStatus = absolutizeSourceStatus(entry.sourceStatus, scanRepoRoot);
  }
  if (entry.artifact) {
    next.artifact = absolutizeArtifact(entry.artifact, scanRepoRoot);
  }
  if (entry.relations && typeof entry.relations === "object") {
    next.relations = { ...entry.relations };
    for (const [key, relation] of Object.entries(entry.relations)) {
      if (!relation || typeof relation !== "object") {
        continue;
      }
      const relationFilePath = path.resolve(rootPath, String(relation.file || ""));
      const nextRelation = {
        ...relation,
        file: absoluteFileRef(relationFilePath),
        rootRelativeFile: relativeFileRef(rootPath, relationFilePath),
      };
      if (relation.url) {
        const relationAssetPath = assetPathFromCatalogUrl(scanRepoRoot, relation.url);
        nextRelation.url = localAssetUrlForPath(relationAssetPath, relation.url, { rootDir });
        nextRelation.assetFile = absoluteFileRef(relationAssetPath);
      }
      next.relations[key] = nextRelation;
    }
  }
  return next;
}

function absolutizeCatalog(catalog, context) {
  return normalizeCatalog({
    ...catalog,
    entries: (Array.isArray(catalog?.entries) ? catalog.entries : [])
      .map((entry) => absolutizeCatalogEntry(entry, context))
      .filter(Boolean),
  });
}

function absolutizeGenerationStatus(status, rootPath) {
  const files = {};
  for (const [file, value] of Object.entries(status?.files || {})) {
    const absolute = absoluteFileRef(path.resolve(rootPath, String(file || "")));
    files[absolute] = {
      ...value,
      file: absolute,
      rootRelativeFile: relativeFileRef(rootPath, absolute),
    };
  }
  return {
    schemaVersion: 1,
    runs: (Array.isArray(status?.runs) ? status.runs : []).map((run) => ({
      ...run,
      files: (Array.isArray(run?.files) ? run.files : [])
        .map((file) => absoluteFileRef(path.resolve(rootPath, String(file || ""))))
        .filter(Boolean),
    })),
    files,
  };
}

export function createLocalAssetBackend({
  workspaceRoot = process.cwd(),
  rootDir = "",
  defaultFile = "",
  githubUrl = "",
  catalogRefreshIntervalMs = 0,
  now = () => Date.now(),
  stepArtifactGenerator = ensureStepTopologyArtifact,
  collisionGenerator = runCadpyInterference,
  sourceFileOpener = defaultSourceFileOpener,
} = {}) {
  const baseWorkspaceRoot = path.resolve(workspaceRoot || process.cwd());
  const defaultRootDir = rootDir
    ? absoluteFileRef(normalizedRootDir(rootDir, baseWorkspaceRoot))
    : absoluteFileRef(baseWorkspaceRoot);
  const catalogCache = new Map();
  const catalogCacheTimes = new Map();

  function setCatalogCache(cacheKey, catalog) {
    catalogCache.set(cacheKey, catalog);
    catalogCacheTimes.set(cacheKey, Number(now()) || 0);
    return catalog;
  }

  function cachedCatalogIsStale(cacheKey) {
    const interval = Number(catalogRefreshIntervalMs) || 0;
    if (interval <= 0) {
      return false;
    }
    const updatedAt = Number(catalogCacheTimes.get(cacheKey)) || 0;
    return (Number(now()) || 0) - updatedAt >= interval;
  }

  function effectiveRootDirForRequest(rootDir = "") {
    return rootDir || defaultRootDir;
  }

  function resolveRoot(rootDir = defaultRootDir) {
    const rootPath = normalizedRootDir(rootDir || defaultRootDir, baseWorkspaceRoot);
    if (!rootPath) {
      throw new Error("CAD Viewer local filesystem requests must include a ?dir= path");
    }
    requireDirectory(rootPath);
    return {
      dir: absoluteFileRef(rootPath),
      rootPath,
      rootName: path.basename(rootPath),
    };
  }

  function resolveRequestRoot({ rootDir = defaultRootDir } = {}) {
    return resolveRoot(effectiveRootDirForRequest(rootDir));
  }

  function scanContextForRoot(resolvedRoot) {
    const rootPath = path.resolve(resolvedRoot.rootPath);
    const scanRepoRoot = pathIsInsideOrEqual(rootPath, baseWorkspaceRoot)
      ? baseWorkspaceRoot
      : rootPath;
    const scanRootDir = scanRepoRoot === rootPath
      ? ""
      : toPosixPath(path.relative(scanRepoRoot, rootPath));
    return {
      rootDir: resolvedRoot.dir,
      rootPath,
      scanRepoRoot,
      scanRootDir,
    };
  }

  function readCatalog({ rootDir: nextRootDir = defaultRootDir, fileRef = "" } = {}) {
    const effectiveRootDir = effectiveRootDirForRequest(nextRootDir);
    const normalizedDir = absoluteFileRef(normalizedRootDir(effectiveRootDir, baseWorkspaceRoot));
    const normalizedFile = normalizedFileRef(fileRef);
    const cacheKey = `dir:${normalizedDir}`;
    if (!catalogCache.has(cacheKey)) {
      return refreshCatalog({ rootDir: normalizedDir, fileRef: normalizedFile });
    }
    if (cachedCatalogIsStale(cacheKey)) {
      return refreshCatalog({ rootDir: normalizedDir, fileRef: normalizedFile });
    }
    if (normalizedDir && normalizedFile) {
      const resolvedRoot = resolveRoot(normalizedDir);
      const requestedPath = filePathFromRef(normalizedFile, resolvedRoot);
      if (requestedPath === resolvedRoot.rootPath || pathIsInside(requestedPath, resolvedRoot.rootPath)) {
        return refreshCatalogForPath({ rootDir: resolvedRoot.dir, filePath: requestedPath });
      }
    }
    return catalogCache.get(cacheKey);
  }

  function readCatalogSafe({ rootDir: nextRootDir = defaultRootDir, fileRef = "" } = {}) {
    try {
      return readCatalog({ rootDir: nextRootDir, fileRef });
    } catch {
      return emptyCatalog();
    }
  }

  function refreshCatalog({ rootDir: nextRootDir = defaultRootDir, fileRef = "" } = {}) {
    const effectiveRootDir = effectiveRootDirForRequest(nextRootDir);
    const resolvedRoot = resolveRoot(effectiveRootDir);
    const context = scanContextForRoot(resolvedRoot);
    const rawCatalog = scanCadDirectory({
      repoRoot: context.scanRepoRoot,
      rootDir: context.scanRootDir,
      includeArtifactStatus: false,
    });
    const catalog = absolutizeCatalog(rawCatalog, context);
    return setCatalogCache(`dir:${resolvedRoot.dir}`, catalog);
  }

  function replaceCatalogEntry(catalog, fileRef, nextEntry) {
    const normalizedRef = normalizedFileRef(fileRef);
    if (!normalizedRef) {
      return normalizeCatalog(catalog);
    }
    const previousEntries = Array.isArray(catalog?.entries) ? catalog.entries : [];
    const entries = previousEntries.filter((entry) => normalizedFileRef(entry?.file) !== normalizedRef);
    if (nextEntry) {
      entries.push(nextEntry);
    }
    return normalizeCatalog({
      ...catalog,
      entries: sortCatalogEntries(entries),
    });
  }

  function refreshCatalogEntryForFile({ rootDir: nextRootDir = defaultRootDir, filePath } = {}) {
    const resolvedRoot = resolveRoot(nextRootDir);
    const context = scanContextForRoot(resolvedRoot);
    const currentCatalog = readCatalog({ rootDir: resolvedRoot.dir });
    const rawEntry = scanCadFile({
      repoRoot: context.scanRepoRoot,
      rootDir: context.scanRootDir,
      filePath,
      includeArtifactStatus: false,
    });
    const nextEntry = rawEntry ? absolutizeCatalogEntry(rawEntry, context) : null;
    const rawFileRef = rawEntry?.file || catalogFileRefForPath({
      repoRoot: context.scanRepoRoot,
      rootDir: context.scanRootDir,
      filePath,
    });
    const fileRef = nextEntry?.file || (rawFileRef ? absoluteFileRef(path.resolve(resolvedRoot.rootPath, rawFileRef)) : absoluteFileRef(filePath));
    const nextCatalog = replaceCatalogEntry(currentCatalog, fileRef, nextEntry);
    return setCatalogCache(`dir:${resolvedRoot.dir}`, nextCatalog);
  }

  function refreshCatalogForPythonSource({ rootDir: nextRootDir = defaultRootDir, filePath } = {}) {
    const resolvedRoot = resolveRoot(nextRootDir);
    const resolvedFilePath = path.resolve(filePath);
    const sourcePath = absoluteFileRef(resolvedFilePath);
    const currentCatalog = readCatalog({ rootDir: resolvedRoot.dir });
    const matchingFileRefs = new Set(
      currentCatalog.entries
        .filter((entry) => normalizedFileRef(entry?.source?.sourcePath || entry?.source?.file) === sourcePath)
        .map((entry) => normalizedFileRef(entry.file))
        .filter(Boolean)
    );
    const sameStemStepPath = path.join(path.dirname(resolvedFilePath), `${path.basename(resolvedFilePath, ".py")}.step`);
    if (sameStemStepPath === resolvedRoot.rootPath || pathIsInside(sameStemStepPath, resolvedRoot.rootPath)) {
      const context = scanContextForRoot(resolvedRoot);
      const rawSameStemEntry = scanCadFile({
        repoRoot: context.scanRepoRoot,
        rootDir: context.scanRootDir,
        filePath: sameStemStepPath,
        includeArtifactStatus: false,
      });
      const sameStemEntry = rawSameStemEntry ? absolutizeCatalogEntry(rawSameStemEntry, context) : null;
      const sameStemFileRef = sameStemEntry?.file || absoluteFileRef(sameStemStepPath);
      if (sameStemEntry || catalogEntryForFileRef(currentCatalog, sameStemFileRef)) {
        matchingFileRefs.add(sameStemFileRef);
      }
    }
    if (!matchingFileRefs.size) {
      return refreshCatalog({ rootDir: resolvedRoot.dir });
    }

    let nextCatalog = currentCatalog;
    const context = scanContextForRoot(resolvedRoot);
    for (const fileRef of matchingFileRefs) {
      const outputPath = path.resolve(fileRef);
      const rawEntry = scanCadFile({
        repoRoot: context.scanRepoRoot,
        rootDir: context.scanRootDir,
        filePath: outputPath,
        includeArtifactStatus: false,
      });
      nextCatalog = replaceCatalogEntry(
        nextCatalog,
        fileRef,
        rawEntry ? absolutizeCatalogEntry(rawEntry, context) : null
      );
    }
    return setCatalogCache(`dir:${resolvedRoot.dir}`, nextCatalog);
  }

  function refreshCatalogForPath({ rootDir: nextRootDir = defaultRootDir, filePath } = {}) {
    const extension = path.extname(String(filePath || "")).toLowerCase();
    if (extension === ".py") {
      return refreshCatalogForPythonSource({ rootDir: nextRootDir, filePath });
    }
    return refreshCatalogEntryForFile({ rootDir: nextRootDir, filePath });
  }

  function filePathFromRef(fileRef, resolvedRoot) {
    const normalized = normalizedFileRef(fileRef);
    if (!normalized) {
      return "";
    }
    return path.isAbsolute(normalized)
      ? path.resolve(normalized)
      : path.resolve(resolvedRoot.rootPath, normalized);
  }

  function resolveStepSource(fileRef, { resolvedRoot = resolveRequestRoot({ fileRef }), catalog = null } = {}) {
    const normalizedRef = normalizedFileRef(fileRef);
    if (!normalizedRef) {
      throw new Error("Missing STEP file");
    }

    const candidates = path.isAbsolute(normalizedRef)
      ? [
          path.resolve(normalizedRef),
          path.resolve(resolvedRoot.rootPath, normalizedRef.replace(/^\/+/, "")),
        ]
      : [
          path.resolve(resolvedRoot.rootPath, normalizedRef),
        ];

    for (const candidatePath of [...new Set(candidates)]) {
      if (
        (candidatePath === resolvedRoot.rootPath || pathIsInside(candidatePath, resolvedRoot.rootPath)) &&
        fs.existsSync(candidatePath)
      ) {
        const extension = path.extname(candidatePath).toLowerCase();
        if (extension === ".py") {
          if (!fileHasGenStep(candidatePath)) {
            throw new Error(`Python generator is not a gen_step() source: ${normalizedRef}`);
          }
          return {
            stepPath: path.join(path.dirname(candidatePath), `${path.basename(candidatePath, extension)}.step`),
            sourcePath: candidatePath,
            skipStepWrite: true,
          };
        }
        if (extension !== ".step" && extension !== ".stp") {
          throw new Error("Only STEP/STP sources or same-stem Python generators can generate STEP topology artifacts");
        }
        const generatorPath = sameStemPythonGeneratorPath(candidatePath);
        return {
          stepPath: candidatePath,
          sourcePath: generatorPath,
          skipStepWrite: Boolean(generatorPath),
        };
      }
    }

    const candidatePath = candidates.find((candidate) => (
      candidate === resolvedRoot.rootPath || pathIsInside(candidate, resolvedRoot.rootPath)
    ));
    if (candidatePath) {
      const extension = path.extname(candidatePath).toLowerCase();
      const generatorPath = sameStemPythonGeneratorPath(candidatePath);
      if ((extension === ".step" || extension === ".stp") && generatorPath) {
        return { stepPath: candidatePath, sourcePath: generatorPath, skipStepWrite: true };
      }
      throw new Error(`STEP file not found: ${normalizedRef}`);
    }
    throw new Error("Requested STEP file is outside the active CAD Viewer root");
  }

  function resolveStepSourceStatus(fileRef, { resolvedRoot = resolveRequestRoot({ fileRef }), catalog = null } = {}) {
    try {
      return resolveStepSource(fileRef, { resolvedRoot, catalog });
    } catch (error) {
      const normalizedRef = normalizedFileRef(fileRef);
      if (!normalizedRef) {
        throw error;
      }
      const candidatePath = filePathFromRef(normalizedRef, resolvedRoot);
      if (!(candidatePath === resolvedRoot.rootPath || pathIsInside(candidatePath, resolvedRoot.rootPath))) {
        throw error;
      }
      const extension = path.extname(candidatePath).toLowerCase();
      if (extension !== ".step" && extension !== ".stp") {
        throw error;
      }
      const generatorPath = sameStemPythonGeneratorPath(candidatePath);
      return {
        stepPath: candidatePath,
        sourcePath: generatorPath,
        skipStepWrite: Boolean(generatorPath),
      };
    }
  }

  function requireCatalogEntryForFileRef(fileRef, {
    resolvedRoot = resolveRequestRoot({ fileRef }),
    rootDir: nextRootDir = defaultRootDir,
    catalog = null,
  } = {}) {
    const normalizedRef = normalizedFileRef(fileRef);
    if (!normalizedRef) {
      throw new Error("Missing file");
    }

    const currentCatalog = catalog || readCatalogSafe({ rootDir: nextRootDir, fileRef: normalizedRef });
    const entry = catalogEntryForFileRef(currentCatalog, normalizedRef);
    if (!entry) {
      throw new Error(`CAD catalog entry not found: ${normalizedRef}`);
    }
    return { entry, relativeFileRef: normalizedRef, currentCatalog, resolvedRoot };
  }

  function resolveOutputFilePath(fileRef, options = {}) {
    const { entry, relativeFileRef, resolvedRoot } = requireCatalogEntryForFileRef(fileRef, options);
    const outputRef = normalizedFileRef(entry?.file || relativeFileRef);
    const outputPath = filePathFromRef(outputRef, resolvedRoot);
    ensurePathInsideRoot(outputPath, resolvedRoot);
    if (!fs.existsSync(outputPath) || !fs.statSync(outputPath).isFile()) {
      throw new Error(`Output file not found: ${outputRef || relativeFileRef}`);
    }
    return outputPath;
  }

  function artifactFileRefFromEntry(entry) {
    const explicitAssetFile = normalizedFileRef(entry?.assetFile || entry?.asset?.file || entry?.artifactFile || entry?.artifact?.file);
    if (explicitAssetFile) {
      return explicitAssetFile;
    }
    const rawUrl = String(entry?.url || "").trim();
    if (!rawUrl) {
      throw new Error("Artifact asset is not available for this file");
    }
    const assetPath = assetPathFromCatalogUrl("/", rawUrl);
    return absoluteFileRef(assetPath);
  }

  function resolveArtifactFilePath(fileRef, options = {}) {
    const { entry, relativeFileRef, resolvedRoot } = requireCatalogEntryForFileRef(fileRef, options);
    const artifactRef = artifactFileRefFromEntry(entry);
    if (!artifactRef) {
      throw new Error(`Artifact asset is not available for ${relativeFileRef}`);
    }
    const artifactPath = filePathFromRef(artifactRef, resolvedRoot);
    ensurePathInsideRoot(artifactPath, resolvedRoot);
    if (!fs.existsSync(artifactPath) || !fs.statSync(artifactPath).isFile()) {
      throw new Error(`Artifact file not found: ${artifactRef}`);
    }
    return artifactPath;
  }

  function resolveSourceCodeFilePath(fileRef, options = {}) {
    const { entry, relativeFileRef, currentCatalog, resolvedRoot } = requireCatalogEntryForFileRef(fileRef, options);
    const explicitSourceRef = normalizedFileRef(entry?.source?.file || entry?.sourceFile || "");
    if (explicitSourceRef) {
      const sourceCandidates = [
        filePathFromRef(explicitSourceRef, resolvedRoot),
        path.resolve(baseWorkspaceRoot, explicitSourceRef),
      ];
      for (const sourcePath of [...new Set(sourceCandidates)]) {
        if (
          (sourcePath === resolvedRoot.rootPath || pathIsInside(sourcePath, resolvedRoot.rootPath)) &&
          fs.existsSync(sourcePath) &&
          fs.statSync(sourcePath).isFile()
        ) {
          return sourcePath;
        }
      }
    }
    const extension = path.extname(relativeFileRef).toLowerCase();
    if (extension === ".step" || extension === ".stp") {
      const { stepPath, sourcePath } = resolveStepSourceStatus(relativeFileRef, { resolvedRoot, catalog: currentCatalog });
      if (sourcePath && fs.existsSync(sourcePath) && fs.statSync(sourcePath).isFile()) {
        ensurePathInsideRoot(sourcePath, resolvedRoot);
        return sourcePath;
      }
      ensurePathInsideRoot(stepPath, resolvedRoot);
    }

    throw new Error(`Source code is not available for ${relativeFileRef}`);
  }

  function resolveFileAssetAccess({
    fileRef,
    asset = "output",
    resolvedRoot = resolveRequestRoot({ fileRef }),
    rootDir: nextRootDir = defaultRootDir,
    catalog = null,
  } = {}) {
    const assetKind = normalizedFileAssetKind(asset);
    const filePath = assetKind === "source"
      ? resolveSourceCodeFilePath(fileRef, { resolvedRoot, rootDir: nextRootDir, catalog })
      : assetKind === "artifact"
        ? resolveArtifactFilePath(fileRef, { resolvedRoot, rootDir: nextRootDir, catalog })
        : resolveOutputFilePath(fileRef, { resolvedRoot, rootDir: nextRootDir, catalog });
    return {
      asset: assetKind,
      file: absoluteFileRef(filePath),
      rootRelativeFile: relativeFileRef(resolvedRoot.rootPath, filePath),
      path: filePath,
      filename: path.basename(filePath),
      contentType: contentTypeForPath(filePath),
    };
  }

  async function openFileAsset(request = {}) {
    const access = resolveFileAssetAccess(request);
    await sourceFileOpener(access.path);
    return {
      asset: access.asset,
      file: access.file,
      filename: access.filename,
      opened: true,
    };
  }

  function resolveSourceFileAccess(request = {}) {
    return resolveFileAssetAccess({ ...request, asset: "source" });
  }

  async function openSourceFile(request = {}) {
    return openFileAsset({ ...request, asset: "source" });
  }

  async function generateStepArtifact({ fileRef, force = false, resolvedRoot = resolveRequestRoot({ fileRef }), catalog = null } = {}) {
    const { stepPath, sourcePath, skipStepWrite } = resolveStepSource(fileRef, { resolvedRoot, catalog });
    const normalizedRef = normalizedFileRef(fileRef);
    const currentCatalog = catalog || readCatalogSafe({ rootDir: resolvedRoot.dir, fileRef: normalizedRef });
    const entry = catalogEntryForFileRef(currentCatalog, normalizedRef);
    const context = scanContextForRoot(resolvedRoot);
    if (
      sourcePath ||
      entryIsPythonBackedStep(entry) ||
      stepFileHasAvailablePythonSourceMetadata(stepPath, context.scanRepoRoot)
    ) {
      throw new Error(
        "CAD Viewer only regenerates GLB artifacts for imported STEP files. Regenerate Python-backed STEP files with their generator script."
      );
    }
    const result = await stepArtifactGenerator({
      repoRoot: context.scanRepoRoot,
      stepPath,
      sourcePath,
      force,
      skipStepWrite,
      writeStepAfterArtifact: Boolean(skipStepWrite),
    });
    return {
      ok: Boolean(result?.ok),
      error: result?.ok ? "" : stepArtifactGenerationError(result),
      result,
      stepPath,
    };
  }

  async function generateCollisions({
    fileRef,
    options = {},
    resolvedRoot = resolveRequestRoot({ fileRef }),
    rootDir: nextRootDir = defaultRootDir,
    catalog = null,
  } = {}) {
    const stepPath = resolveOutputFilePath(fileRef, { resolvedRoot, rootDir: nextRootDir, catalog });
    const extension = path.extname(stepPath).toLowerCase();
    if (extension !== ".step" && extension !== ".stp") {
      throw new Error("Collision detection requires a STEP/STP file");
    }
    const context = scanContextForRoot(resolvedRoot);
    return collisionGenerator({
      repoRoot: context.scanRepoRoot,
      stepPath,
      options,
    });
  }

  function readStepSourceStatusForFile({ fileRef, resolvedRoot = resolveRequestRoot({ fileRef }), catalog = null } = {}) {
    const { stepPath, sourcePath } = resolveStepSourceStatus(fileRef, { resolvedRoot, catalog });
    const context = scanContextForRoot(resolvedRoot);
    const status = readStepSourceStatus({
      repoRoot: context.scanRepoRoot,
      stepPath,
      pythonSourcePath: sourcePath,
    });
    return absolutizeSourceStatus({
      ...status,
      ...(status?.artifact ? { artifact: absolutizeArtifact(status.artifact, context.scanRepoRoot) } : {}),
    }, context.scanRepoRoot);
  }

  function readGeneratorStatus({ rootDir: nextRootDir = defaultRootDir } = {}) {
    const resolvedRoot = resolveRoot(effectiveRootDirForRequest(nextRootDir));
    const context = scanContextForRoot(resolvedRoot);
    return absolutizeGenerationStatus(readGenerationStatus({
      repoRoot: context.scanRepoRoot,
      rootDir: context.scanRootDir,
    }), resolvedRoot.rootPath);
  }

  function generationStatusDir(rootDir = defaultRootDir) {
    const resolvedRoot = resolveRoot(effectiveRootDirForRequest(rootDir));
    const context = scanContextForRoot(resolvedRoot);
    return resolveGenerationStatusDir(context.scanRepoRoot, context.scanRootDir);
  }

  function isGenerationStatusPath(filePath, rootDir = defaultRootDir) {
    const resolvedRoot = resolveRoot(effectiveRootDirForRequest(rootDir));
    const resolvedPath = path.resolve(filePath);
    const name = path.basename(resolvedPath);
    return (
      (resolvedPath === resolvedRoot.rootPath || pathIsInside(resolvedPath, resolvedRoot.rootPath)) &&
      name.startsWith(".") &&
      name.endsWith(".generation.lock.json")
    );
  }

  function entryForSourcePath(catalog, resolvedRoot, sourcePath) {
    const fileRef = absoluteFileRef(sourcePath);
    return Array.isArray(catalog?.entries)
      ? catalog.entries.find((entry) => normalizedFileRef(entry?.file) === fileRef) || null
      : null;
  }

  function assetPathForFileRef(fileRef, { resolvedRoot = null, rootDir = "" } = {}) {
    const normalizedRef = normalizedFileRef(fileRef);
    if (!normalizedRef || !path.isAbsolute(normalizedRef)) {
      return null;
    }
    const candidatePath = path.resolve(normalizedRef);
    if (!isServedCadAsset(candidatePath)) {
      return null;
    }
    const activeRoot = resolvedRoot || (rootDir ? resolveRoot(rootDir) : null);
    if (activeRoot && !(candidatePath === activeRoot.rootPath || pathIsInside(candidatePath, activeRoot.rootPath))) {
      const error = new Error("Forbidden");
      error.statusCode = 403;
      throw error;
    }
    return candidatePath;
  }

  async function writeAsset({ fileRef, body, resolvedRoot = resolveRequestRoot({ fileRef }) } = {}) {
    const normalizedRef = normalizedFileRef(fileRef);
    if (!normalizedRef) {
      throw new Error("Missing asset path");
    }
    const filePath = filePathFromRef(normalizedRef, resolvedRoot);
    if (!(filePath === resolvedRoot.rootPath || pathIsInside(filePath, resolvedRoot.rootPath))) {
      throw new Error("Asset writes must stay inside the active CAD Viewer root");
    }
    if (!isServedCadAsset(filePath)) {
      throw new Error(`Unsupported CAD Viewer asset write: ${normalizedRef}`);
    }
    const bytes = Buffer.isBuffer(body) ? body : Buffer.from(body || "");
    fs.mkdirSync(path.dirname(filePath), { recursive: true });
    fs.writeFileSync(filePath, bytes);
    return {
      path: filePath,
      bytes: bytes.length,
      contentType: contentTypeForPath(filePath),
    };
  }

  return {
    kind: "local-fs",
    canGenerateStepArtifacts: true,
    repoRoot: baseWorkspaceRoot,
    rootDir: "",
    defaultFile,
    githubUrl,
    resolveRoot,
    resolveRequestRoot,
    readCatalog,
    readCatalogSafe,
    refreshCatalog,
    refreshCatalogForPath,
    resolveStepSource,
    readStepSourceStatus: readStepSourceStatusForFile,
    resolveFileAssetAccess,
    openFileAsset,
    resolveSourceFileAccess,
    openSourceFile,
    readGenerationStatus: readGeneratorStatus,
    generationStatusDir,
    isGenerationStatusPath,
    generateStepArtifact,
    generateCollisions,
    entryForSourcePath,
    assetPathForFileRef,
    writeAsset,
    contentTypeForPath,
  };
}

export { contentTypeForPath };
