import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";

import {
  CAD_CATALOG_SCHEMA_VERSION,
  catalogFileRefForPath,
  isCadgenPackageDescriptorPath,
  isServedCadAsset,
  readStepSourceStatus,
  scanCadDirectory,
  scanCadFile,
  filesystemPathIdentity,
  sortCatalogEntries,
} from "./catalog/cadDirectoryScanner.mjs";
import {
  generationStatusDir as resolveGenerationStatusDir,
  readGenerationStatus,
} from "./catalog/generationStatus.mjs";
import { pathIsInside } from "cadjs/lib/pathUtils.mjs";
import { ensureStepTopologyArtifact } from "./step/stepArtifactCompiler.mjs";

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

// Fail-closed real-containment check that resists TOCTOU/symlink-swap
// attacks against ``/__cad/asset``. Lexical containment (``pathIsInside``)
// only checks the raw string, so after the catalog scan an attacker who
// can write inside the served tree can replace a validated leaf (or any
// ancestor) with a symlink/junction/reparse-point pointing outside the
// trusted root and the plain ``fs.createReadStream(path)`` would follow
// it. This helper:
//   1. rejects symlinks at the leaf via ``lstat`` (Node also reports
//      Windows junctions/reparse points as symbolic links here),
//   2. requires the target to resolve to a regular file,
//   3. resolves BOTH the leaf and the trusted root via ``realpath`` --
//      so any symlink ancestor along the target's path is followed and
//      re-compared against the trusted root's real path.
// Callers MUST perform this check right before they open the file /
// hand the path to ``serveStaticFile``. ``serveStaticFile`` in turn
// re-checks with ``lstat`` before streaming to shrink the residual
// check-vs-open race.
function assertSafeAssetContainment(filePath, resolvedRoot) {
  ensurePathInsideRoot(filePath, resolvedRoot);
  let leafStat;
  try {
    leafStat = fs.lstatSync(filePath);
  } catch {
    const error = new Error("Asset file not found");
    error.statusCode = 404;
    throw error;
  }
  if (leafStat.isSymbolicLink()) {
    const error = new Error("Symbolic links are not served as CAD Viewer assets");
    error.statusCode = 403;
    throw error;
  }
  if (!leafStat.isFile()) {
    const error = new Error("Requested asset is not a regular file");
    error.statusCode = 403;
    throw error;
  }
  let realFile;
  let realRoot;
  try {
    realFile = fs.realpathSync(filePath);
    realRoot = fs.realpathSync(resolvedRoot.rootPath);
  } catch {
    const error = new Error("Asset file cannot be resolved");
    error.statusCode = 404;
    throw error;
  }
  const rel = path.relative(realRoot, realFile);
  // Segment-aware containment: reject empty (== root), absolute
  // (different drive on Windows), and a literal ``..`` first segment
  // (real escape). ``..part.step`` is a legal FILENAME whose relative
  // component merely starts with two dots and must NOT be rejected.
  const firstSegment = rel.split(/[\\/]/, 1)[0];
  if (rel === "" || firstSegment === ".." || path.isAbsolute(rel)) {
    const error = new Error("Requested file is outside the active CAD Viewer root");
    error.statusCode = 403;
    throw error;
  }
  return realFile;
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
  directoryRoot = process.cwd(),
  rootDir = "",
  defaultFile = "",
  githubUrl = "",
  stepArtifactGenerator = ensureStepTopologyArtifact,
  sourceFileOpener = defaultSourceFileOpener,
} = {}) {
  const baseDirectoryRoot = path.resolve(directoryRoot || process.cwd());
  const defaultRootDir = rootDir
    ? absoluteFileRef(normalizedRootDir(rootDir, baseDirectoryRoot))
    : absoluteFileRef(baseDirectoryRoot);
  const catalogCache = new Map();
  // Authoritative "logical STEP -> ambiguous" set from the last full
  // ``scanCadDirectory`` scan, keyed by ``dir:${resolvedRoot.dir}``.
  // Threaded into every per-file ``scanCadFile`` so a request-shaped
  // incremental refresh can only STRENGTHEN a mapping to error --
  // never downgrade an authoritative ``ambiguous_package_binding``
  // back to an accepted binding when the STEP's own metadata names
  // only one of the two conflicting packages.  Only a subsequent full
  // ``refreshCatalog`` (triggered by an initial load, a package
  // descriptor change, or an explicit invalidation) reconciles the
  // ambiguity set with the current on-disk descriptor state.
  const authoritativeAmbiguousStepsByDir = new Map();

  function effectiveRootDirForRequest(rootDir = "") {
    return rootDir || defaultRootDir;
  }

  function resolveRoot(rootDir = defaultRootDir) {
    const rootPath = normalizedRootDir(rootDir || defaultRootDir, baseDirectoryRoot);
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
    const scanRepoRoot = pathIsInsideOrEqual(rootPath, baseDirectoryRoot)
      ? baseDirectoryRoot
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
    const normalizedDir = absoluteFileRef(normalizedRootDir(effectiveRootDir, baseDirectoryRoot));
    const normalizedFile = normalizedFileRef(fileRef);
    const cacheKey = `dir:${normalizedDir}`;
    if (!catalogCache.has(cacheKey)) {
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
      includeArtifactStatus: true,
    });
    const cacheKey = `dir:${resolvedRoot.dir}`;
    // Capture the authoritative ambiguity set from the raw scan
    // BEFORE ``absolutizeCatalog`` (which runs ``normalizeCatalog`` and
    // drops any field outside {schemaVersion, entries}). Stored as a
    // Set of filesystem-identity keys (case-folded on Windows).
    const authoritativePaths = Array.isArray(rawCatalog?.authoritativeAmbiguousStepPaths)
      ? new Set(rawCatalog.authoritativeAmbiguousStepPaths.map((p) => filesystemPathIdentity(String(p || ""))))
      : new Set();
    authoritativeAmbiguousStepsByDir.set(cacheKey, authoritativePaths);
    const catalog = absolutizeCatalog(rawCatalog, context);
    catalogCache.set(cacheKey, catalog);
    return catalog;
  }

  function authoritativeAmbiguousStepsForDir(resolvedRoot) {
    return authoritativeAmbiguousStepsByDir.get(`dir:${resolvedRoot.dir}`) || null;
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
      includeArtifactStatus: true,
      authoritativeAmbiguousSteps: authoritativeAmbiguousStepsForDir(resolvedRoot),
    });
    const nextEntry = rawEntry ? absolutizeCatalogEntry(rawEntry, context) : null;
    const rawFileRef = rawEntry?.file || catalogFileRefForPath({
      repoRoot: context.scanRepoRoot,
      rootDir: context.scanRootDir,
      filePath,
    });
    const fileRef = nextEntry?.file || (rawFileRef ? absoluteFileRef(path.resolve(resolvedRoot.rootPath, rawFileRef)) : absoluteFileRef(filePath));
    const nextCatalog = replaceCatalogEntry(currentCatalog, fileRef, nextEntry);
    catalogCache.set(`dir:${resolvedRoot.dir}`, nextCatalog);
    return nextCatalog;
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
        includeArtifactStatus: true,
        authoritativeAmbiguousSteps: authoritativeAmbiguousStepsForDir(resolvedRoot),
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
        includeArtifactStatus: true,
        authoritativeAmbiguousSteps: authoritativeAmbiguousStepsForDir(resolvedRoot),
      });
      nextCatalog = replaceCatalogEntry(
        nextCatalog,
        fileRef,
        rawEntry ? absolutizeCatalogEntry(rawEntry, context) : null
      );
    }
    catalogCache.set(`dir:${resolvedRoot.dir}`, nextCatalog);
    return nextCatalog;
  }

  function refreshCatalogForPath({ rootDir: nextRootDir = defaultRootDir, filePath } = {}) {
    // A change to a cadgen package descriptor (add/remove a package,
    // switch its ``stepPath`` or ``sourcePath``) may alter the
    // authoritative "logical STEP -> ambiguous" state that per-file
    // targeted refresh cannot re-derive from a single STEP file.
    // Route these events to a full ``refreshCatalog`` so the
    // ambiguity set is reconciled with the current descriptor state.
    if (isCadgenPackageDescriptorPath(filePath)) {
      return refreshCatalog({ rootDir: nextRootDir });
    }
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

  // Discover a python source bound to ``stepAbsPath`` via the trusted
  // catalog, NOT via client-supplied metadata. The scanner has already
  // routed every STEP entry through ``bindCadgenPackage``, so the
  // ``entry.source.file`` field on a python-kind catalog entry is
  // authoritative. Returns "" when no such binding exists.
  //
  // ``readCatalog`` may run a per-file incremental refresh that
  // returns null when the STEP file is missing (descriptor-only
  // mapping). To avoid dropping the authoritative binding we look up
  // the caller-supplied catalog first, then the cached full-scan
  // catalog, and finally trigger a full ``refreshCatalog`` -- never
  // the per-file incremental path.
  function catalogBoundPythonSource(fileRef, resolvedRoot, catalog) {
    const cacheKey = `dir:${resolvedRoot.dir}`;
    const activeCatalog = catalog
      || catalogCache.get(cacheKey)
      || (() => {
        try {
          return refreshCatalog({ rootDir: resolvedRoot.dir });
        } catch {
          return null;
        }
      })();
    if (!activeCatalog || !Array.isArray(activeCatalog.entries)) {
      return "";
    }
    const entry = catalogEntryForFileRef(activeCatalog, fileRef);
    if (!entry) return "";
    if (String(entry.sourceKind || "").toLowerCase() !== "python") return "";
    const rawSource = entry.source?.file || entry.source?.sourcePath || "";
    if (!rawSource) return "";
    const abs = normalizedFileRef(rawSource);
    if (!abs) return "";
    const candidate = path.isAbsolute(abs)
      ? path.resolve(abs)
      : path.resolve(resolvedRoot.rootPath, abs);
    if (!(candidate === resolvedRoot.rootPath || pathIsInside(candidate, resolvedRoot.rootPath))) {
      return "";
    }
    try {
      if (!fs.statSync(candidate).isFile()) return "";
    } catch {
      return "";
    }
    return candidate;
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
        // A catalog-bound source has already passed the package
        // identity checks and is authoritative. Only use same-stem
        // inference when no such package binding exists.
        const boundGenerator = catalogBoundPythonSource(fileRef, resolvedRoot, catalog);
        const generatorPath = boundGenerator || sameStemPythonGeneratorPath(candidatePath);
        return {
          stepPath: candidatePath,
          sourcePath: generatorPath,
          skipStepWrite: Boolean(generatorPath),
          fromCatalogBinding: Boolean(boundGenerator),
        };
      }
    }

    const candidatePath = candidates.find((candidate) => (
      candidate === resolvedRoot.rootPath || pathIsInside(candidate, resolvedRoot.rootPath)
    ));
    if (candidatePath) {
      const extension = path.extname(candidatePath).toLowerCase();
      // Descriptor-only non-same-stem mapping: STEP file is missing,
      // and the trusted catalog binding says a python source elsewhere
      // in the tree produces this STEP. It is authoritative and must
      // win over an unrelated same-stem heuristic candidate. Use it
      // and request one-pass regeneration. The
      // ``fromCatalogBinding`` flag distinguishes this case from
      // ``sameStemPythonGeneratorPath`` fallback so ``generateStepArtifact``
      // can allow missing-STEP regeneration ONLY when the catalog
      // authoritatively says the STEP will be produced.
      if (extension === ".step" || extension === ".stp") {
        const boundGenerator = catalogBoundPythonSource(fileRef, resolvedRoot, catalog);
        if (boundGenerator) {
          return {
            stepPath: candidatePath,
            sourcePath: boundGenerator,
            skipStepWrite: false,
            fromCatalogBinding: true,
          };
        }
        const sameStemGenerator = sameStemPythonGeneratorPath(candidatePath);
        if (sameStemGenerator) {
          return { stepPath: candidatePath, sourcePath: sameStemGenerator, skipStepWrite: true };
        }
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
    // ``assertSafeAssetContainment`` does the lstat + realpath check
    // that closes the symlink-swap escape past lexical containment.
    assertSafeAssetContainment(outputPath, resolvedRoot);
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
    assertSafeAssetContainment(artifactPath, resolvedRoot);
    return artifactPath;
  }

  function resolveSourceCodeFilePath(fileRef, options = {}) {
    const { entry, relativeFileRef, currentCatalog, resolvedRoot } = requireCatalogEntryForFileRef(fileRef, options);
    const explicitSourceRef = normalizedFileRef(entry?.source?.file || entry?.sourceFile || "");
    if (explicitSourceRef) {
      const sourceCandidates = [
        filePathFromRef(explicitSourceRef, resolvedRoot),
        path.resolve(baseDirectoryRoot, explicitSourceRef),
      ];
      for (const sourcePath of [...new Set(sourceCandidates)]) {
        if (sourcePath === resolvedRoot.rootPath || pathIsInside(sourcePath, resolvedRoot.rootPath)) {
          try {
            assertSafeAssetContainment(sourcePath, resolvedRoot);
            return sourcePath;
          } catch {
            // Try the next candidate; a symlink here MUST NOT be
            // followed but a sibling lexical candidate might still
            // resolve to a valid regular file under the real root.
            continue;
          }
        }
      }
    }
    const extension = path.extname(relativeFileRef).toLowerCase();
    if (extension === ".step" || extension === ".stp") {
      const { stepPath, sourcePath } = resolveStepSourceStatus(relativeFileRef, { resolvedRoot, catalog: currentCatalog });
      if (sourcePath) {
        try {
          assertSafeAssetContainment(sourcePath, resolvedRoot);
          return sourcePath;
        } catch {
          // Fall through -- the STEP itself will be checked below.
        }
      }
      assertSafeAssetContainment(stepPath, resolvedRoot);
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
    const resolvedSource = resolveStepSource(fileRef, { resolvedRoot, catalog });
    const { stepPath, sourcePath: boundSource, fromCatalogBinding = false } = resolvedSource;
    // Regeneration refuses ambiguous descriptor mappings outright.
    // The authoritative source of truth is (a) the catalog entry's
    // ``ambiguous_package_binding`` diagnostic, and (b) the last full
    // scan's authoritative ambiguous set.  Either being present means
    // two descriptors both claim this logical STEP -- no candidate is
    // trusted, so the generator MUST NOT be invoked.
    const activeCatalog = catalog || catalogCache.get(`dir:${resolvedRoot.dir}`) || null;
    const resolvedStepPath = path.resolve(stepPath);
    const resolvedStepKey = filesystemPathIdentity(resolvedStepPath);
    const authoritative = authoritativeAmbiguousStepsForDir(resolvedRoot);
    const catalogAmbiguity = activeCatalog
      && Array.isArray(activeCatalog.entries)
      && activeCatalog.entries.some((entry) => (
        String(entry?.artifact?.error || "") === "ambiguous_package_binding"
        && filesystemPathIdentity(String(entry?.file || "")) === resolvedStepKey
      ));
    if (catalogAmbiguity || (authoritative && authoritative.has(resolvedStepKey))) {
      throw new Error(
        `CAD Viewer refuses to regenerate ${fileRef}: multiple cadgen packages claim this logical STEP (ambiguous_package_binding).`,
      );
    }
    const extension = path.extname(stepPath).toLowerCase();
    if (extension !== ".step" && extension !== ".stp") {
      throw new Error("CAD Viewer only regenerates GLB artifacts for existing STEP/STP files.");
    }
    let hasStepFile = false;
    try {
      hasStepFile = fs.statSync(stepPath).isFile();
    } catch {
      hasStepFile = false;
    }
    // Missing STEP file is only accepted when the trusted catalog
    // binding authoritatively says a python source produces it -- the
    // reviewer-mandated non-same-stem regeneration path. Same-stem
    // inference alone (``sameStemPythonGeneratorPath`` beside a missing
    // STEP) still fails closed with the historic diagnostic so the
    // client cannot silently trigger a build that was never registered
    // in the catalog.
    if (!hasStepFile && !fromCatalogBinding) {
      throw new Error("CAD Viewer only regenerates GLB artifacts for existing STEP/STP files.");
    }
    const writeStepAfterArtifact = fromCatalogBinding && Boolean(boundSource);
    const context = scanContextForRoot(resolvedRoot);
    // A trusted catalog binding is authoritative even when the STEP
    // already exists. Hand cadgen that explicit Python source and ask
    // it to regenerate STEP + package from one evaluation; otherwise
    // non-same-stem mappings can silently fall back to imported-STEP
    // or same-stem behavior and leave their real package stale.
    const generatorSourcePath = writeStepAfterArtifact ? boundSource : "";
    const result = await stepArtifactGenerator({
      repoRoot: context.scanRepoRoot,
      stepPath,
      sourcePath: generatorSourcePath,
      force,
      skipStepWrite: false,
      writeStepAfterArtifact,
    });
    return {
      ok: Boolean(result?.ok),
      error: result?.ok ? "" : stepArtifactGenerationError(result),
      result,
      stepPath,
    };
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
    if (activeRoot) {
      // Lexical containment first (cheap early-out), then the safe
      // real-containment/symlink-rejection check.
      if (!(candidatePath === activeRoot.rootPath || pathIsInside(candidatePath, activeRoot.rootPath))) {
        const error = new Error("Forbidden");
        error.statusCode = 403;
        throw error;
      }
      // ``assertSafeAssetContainment`` throws HTTP-status-tagged errors;
      // the ``/__cad/asset`` handler in ``httpHandlers.mjs`` routes them
      // through the error path, so a symlink/junction replacement of a
      // scanned component surfaces as 403 rather than serving bytes
      // from outside the tree.
      assertSafeAssetContainment(candidatePath, activeRoot);
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
    repoRoot: baseDirectoryRoot,
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
    entryForSourcePath,
    assetPathForFileRef,
    writeAsset,
    contentTypeForPath,
  };
}

export { contentTypeForPath };
