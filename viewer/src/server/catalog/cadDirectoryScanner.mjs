import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import {
  inlineStepGlbArtifactPathForSource,
  isInlineStepGlbArtifactPath,
  isInlineStepParameterPath,
  isPathInsidePerStepViewerDirectory,
  isPerStepViewerDirectoryName,
  stepParameterPathForStepSource,
} from "cadjs/common/stepSidecars.mjs";
import {
  STEP_EDGE_BARYCENTRIC_ATTRIBUTE,
  STEP_EDGE_CLASS_ATTRIBUTE,
  STEP_EDGE_VISIBILITY_CLASSES,
  STEP_TOPOLOGY_EXTENSION,
  STEP_PACKAGE_SCHEMA_VERSION,
  STEP_TOPOLOGY_SCHEMA_VERSION,
  isCurrentStepTopologySchemaVersion
} from "cadjs/common/stepTopology.mjs";
import { toPosixPath } from "cadjs/lib/pathUtils.mjs";
import { readTextToCadStepMetadataFile } from "../step/stepMetadata.mjs";

export const DEFAULT_VIEWER_ROOT_DIR = "";
export const CAD_CATALOG_SCHEMA_VERSION = 4;

function filesystemTextIdentity(value, platform = process.platform) {
  const text = String(value);
  return platform === "win32" ? text.toLowerCase() : text;
}

export function filesystemPathIdentity(value, { platform = process.platform } = {}) {
  const resolved = path.resolve(value);
  return filesystemTextIdentity(resolved, platform);
}

const SOURCE_EXTENSIONS = new Set([".step", ".stp", ".stl", ".3mf", ".glb", ".gcode", ".dxf", ".urdf", ".srdf", ".sdf"]);
const REGENERATE_STEP_COMMAND = "python -m cadgen.step_artifact_cli --repo-root . --step";

// Canonical cadgen render-package layout constants (mirrors
// packages/cadgen/src/cadgen/catalog.py:16-17). The scanner and the
// STEP artifact compiler both compute the same
// ``<entry parent>/__cadgen__/models/<entry filename>`` path from an
// entry file, so the URL/validation of a STEP entry is bound to the
// package cadgen wrote -- no separately-trusted metadata channel.
const CADGEN_DIRNAME = "__cadgen__";
const CADGEN_MODELS_DIRNAME = "models";
const CADGEN_PACKAGE_DESCRIPTOR = "assembly.json";

function relativePathEscapesRoot(relativePath) {
  return relativePath === ".."
    || relativePath.startsWith(`..${path.sep}`)
    || path.isAbsolute(relativePath);
}

function packageDirForEntry(entryPath) {
  const resolved = path.resolve(entryPath);
  return path.resolve(
    path.dirname(resolved),
    CADGEN_DIRNAME,
    CADGEN_MODELS_DIRNAME,
    path.basename(resolved),
  );
}

function _buildPackageEntryMap(rootPath, repoRoot) {
  // Walk every cadgen render-package descriptor under ``rootPath``
  // and bind each ONE authoritative package to (a) a real entry file
  // that ``descriptor.sourcePath`` resolves to exactly, and (b) a
  // logical STEP path derived from ``descriptor.stepPath``. Both
  // sides fail-closed on:
  //   * lexical or realpath escape outside ``trustedRepo``
  //   * a symlink/junction at the package directory or anywhere
  //     along an ancestor of the descriptor's paths
  //   * missing ``sourceKind``/``sourcePath`` (a forged descriptor
  //     at ``__cadgen__/models/<random>/`` with no python source
  //     that resolves back to itself)
  //   * ``sourcePath`` that does not resolve exactly to the package
  //     entry file (the descriptor is not bound to its entry)
  //
  // Returns ``{ map, ambiguous }``. If two descriptors bind to the
  // same logical STEP path, that key goes into ``ambiguous`` and
  // NEVER falls back to inference at any callsite (P1 fail-closed).
  const map = new Map();
  const ambiguous = new Set();
  const rootAbs = path.resolve(rootPath);
  const trustedRepoLex = path.resolve(repoRoot);
  const trustedRepoReal = _safeRealpath(trustedRepoLex);
  if (!trustedRepoReal) {
    return { map, ambiguous };
  }
  const walk = (dir) => {
    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const entry of entries) {
      if (!entry.isDirectory()) continue;
      const fullPath = path.join(dir, entry.name);
      if (entry.name === CADGEN_DIRNAME) {
        _indexCadgenPackages(fullPath, map, ambiguous, trustedRepoLex, trustedRepoReal);
        continue;
      }
      if (shouldSkipDirectory(entry.name)) continue;
      walk(fullPath);
    }
  };
  walk(rootAbs);
  return { map, ambiguous };
}


function _indexCadgenPackages(cadgenDir, map, ambiguous, trustedRepoLex, trustedRepoReal) {
  const modelsDir = path.join(cadgenDir, CADGEN_MODELS_DIRNAME);
  let entries;
  try {
    entries = fs.readdirSync(modelsDir, { withFileTypes: true });
  } catch {
    return;
  }
  const sourceDir = path.dirname(cadgenDir);
  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    const packageDir = path.join(modelsDir, entry.name);
    const entryPath = path.join(sourceDir, entry.name);
    const bound = _bindPackageToEntry({
      entryPath,
      packageDir,
      trustedRepoLex,
      trustedRepoReal,
      identityOnly: true,
    });
    if (!bound) continue;
    const key = filesystemPathIdentity(bound.logicalStepPath);
    if (map.has(key) || ambiguous.has(key)) {
      // TWO descriptors both claim this logical STEP -- fail closed
      // for all consumers, no fallback inference (P1 explicit
      // ambiguity).
      map.delete(key);
      ambiguous.add(key);
      continue;
    }
    map.set(key, bound);
  }
}


// Single trusted package-binding validator used by every seam that
// reads a cadgen render-package descriptor. Everything the reviewer
// asked to fail closed on -- forged package location, symlinked
// ancestors, absent/misbound ``sourcePath``, unsafe ``stepPath``,
// missing identity hashes -- happens here in ONE place so no consumer
// can adopt a partially validated descriptor.
export function bindCadgenPackage({
  packageDir,
  trustedRoot,
  entryPath = null,
}) {
  const detailed = bindCadgenPackageDetailed({ packageDir, trustedRoot, entryPath });
  return detailed.ok ? detailed.binding : null;
}


// Build a targeted ``{map, ambiguous}`` for a single existing STEP
// file without recursing the whole viewer tree. Candidate package
// locations are derived FROM the STEP file itself:
//   * same-stem Python generator's package
//     ``<step.parent>/__cadgen__/models/<step.stem>.py``
//   * imported-STEP-keyed package
//     ``<step.parent>/__cadgen__/models/<step.name>``
//   * python source declared by the STEP's own ``cadgen:sourcePath``
//     metadata (trust-root validated) -- its package sits at
//     ``<py.parent>/__cadgen__/models/<py.name>``
// Each candidate is validated by the same trusted binder used by the
// full scan; ambiguity between candidates fails closed identically.
// The scan touches only these specific package directories -- never
// unrelated ``__cadgen__`` siblings elsewhere in the tree.
// Union the targeted binder's local ``ambiguous`` set with the
// caller-supplied authoritative ambiguity for THIS STEP. Returns the
// SAME set object when no authoritative upgrade applies (identity is
// used by the caller to detect the strengthening). When the
// authoritative set names ``resolvedStep``, returns a new Set
// containing every entry of the local set plus ``resolvedStep``.
function _mergeAuthoritativeAmbiguity(localAmbiguous, authoritativeAmbiguousSteps, resolvedStep) {
  if (!authoritativeAmbiguousSteps || typeof authoritativeAmbiguousSteps.has !== "function") {
    return localAmbiguous;
  }
  const key = filesystemPathIdentity(resolvedStep);
  if (!authoritativeAmbiguousSteps.has(key)) {
    return localAmbiguous;
  }
  if (localAmbiguous && localAmbiguous.has(key)) {
    return localAmbiguous;
  }
  const merged = new Set(localAmbiguous || []);
  merged.add(key);
  return merged;
}


function _targetedPackageBindingForStep(stepAbs, repoRoot) {
  const map = new Map();
  const ambiguous = new Set();
  const resolvedStep = path.resolve(stepAbs);
  const stepKey = filesystemPathIdentity(resolvedStep);
  const parent = path.dirname(resolvedStep);
  const ext = path.extname(resolvedStep);
  const stem = path.basename(resolvedStep, ext);
  const candidates = new Set();
  candidates.add(path.join(parent, CADGEN_DIRNAME, CADGEN_MODELS_DIRNAME, `${stem}.py`));
  candidates.add(path.join(parent, CADGEN_DIRNAME, CADGEN_MODELS_DIRNAME, `${stem}${ext}`));
  try {
    if (fileStats(resolvedStep)) {
      const metadata = readTextToCadStepMetadataFile(resolvedStep);
      const declared = String(metadata?.sourcePath || "").trim();
      if (declared) {
        const declaredSource = resolveTrustedMetadataPath(resolvedStep, declared, repoRoot);
        if (declaredSource) {
          candidates.add(path.join(
            path.dirname(declaredSource),
            CADGEN_DIRNAME,
            CADGEN_MODELS_DIRNAME,
            path.basename(declaredSource),
          ));
        }
      }
    }
  } catch {
    // Unreadable STEP metadata is not a hard error -- just skip the
    // metadata-declared candidate. Same-stem / imported candidates
    // are unaffected.
  }
  for (const packageDir of candidates) {
    const bound = bindCadgenPackage({ packageDir, trustedRoot: repoRoot });
    if (!bound) continue;
    if (filesystemPathIdentity(bound.logicalStepPath) !== stepKey) continue;
    if (map.has(stepKey) || ambiguous.has(stepKey)) {
      map.delete(stepKey);
      ambiguous.add(stepKey);
      continue;
    }
    map.set(stepKey, bound);
  }
  return { map, ambiguous };
}


// Diagnostic variant: same acceptance rules as ``bindCadgenPackage``
// but returns ``{ok: false, code, reason, details?}`` on refusal so
// consumers (e.g. ``validateStepTopologyArtifact``) can surface
// actionable error codes without duplicating any acceptance logic.
export function bindCadgenPackageDetailed({
  packageDir,
  trustedRoot,
  entryPath = null,
  identityOnly = false,
}) {
  const trustedRepoLex = path.resolve(trustedRoot);
  const trustedRepoReal = _safeRealpath(trustedRepoLex);
  if (!trustedRepoReal) {
    return { ok: false, code: "missing_package", reason: `Trusted root ${trustedRoot} cannot be resolved` };
  }
  const cadgenDir = path.dirname(packageDir);
  if (path.basename(cadgenDir) !== CADGEN_MODELS_DIRNAME) {
    return { ok: false, code: "missing_package", reason: `Package ${packageDir} is not under ${CADGEN_MODELS_DIRNAME}/` };
  }
  if (path.basename(path.dirname(cadgenDir)) !== CADGEN_DIRNAME) {
    return { ok: false, code: "missing_package", reason: `Package ${packageDir} is not under ${CADGEN_DIRNAME}/${CADGEN_MODELS_DIRNAME}/` };
  }
  const derivedEntry = path.join(
    path.dirname(path.dirname(cadgenDir)),
    path.basename(packageDir),
  );
  const boundEntry = entryPath ? path.resolve(entryPath) : derivedEntry;
  if (filesystemPathIdentity(boundEntry) !== filesystemPathIdentity(derivedEntry)) {
    return {
      ok: false,
      code: "missing_package",
      reason: `Requested entryPath ${boundEntry} is not the canonical entry ${derivedEntry} for ${packageDir}`,
    };
  }
  return _bindPackageToEntryDetailed({
    entryPath: boundEntry,
    packageDir,
    trustedRepoLex,
    trustedRepoReal,
    identityOnly,
  });
}

// Resolve only the immutable package identity (canonical package,
// entry, sourcePath and logical stepPath). This deliberately ignores
// artifact schema/freshness/components so a stale package can still
// identify the safe rebuild target without becoming publishable.
export function bindCadgenPackageIdentity(args) {
  const detailed = bindCadgenPackageDetailed({ ...args, identityOnly: true });
  return detailed.ok ? detailed.binding : null;
}


// Diagnostic bind: returns ``{ok: true, binding}`` on acceptance and
// ``{ok: false, code, reason}`` on any refusal. ``bindCadgenPackage``
// hides the diagnostics behind the historic ``binding | null`` contract
// so callers that only care about "did it accept" stay simple.
function _bindPackageToEntryDetailed({
  entryPath,
  packageDir,
  trustedRepoLex,
  trustedRepoReal,
  identityOnly = false,
}) {
  const refuse = (code, reason) => ({ ok: false, code, reason });
  // Package containment: no symlinks at the package dir, realpath
  // stays under ``trustedRepoReal``, lexical == realpath relative
  // (defeats any junction on the ancestor chain).
  let lst;
  try {
    lst = fs.lstatSync(packageDir);
  } catch {
    return refuse("missing_package", `Package directory ${packageDir} is missing`);
  }
  if (lst.isSymbolicLink()) {
    return refuse(
      "missing_package",
      `Package directory ${packageDir} is a symbolic link; refusing to follow`,
    );
  }
  if (!lst.isDirectory()) {
    return refuse("missing_package", `Package location ${packageDir} is not a directory`);
  }
  const realPackage = _safeRealpath(packageDir);
  if (!realPackage) {
    return refuse(
      "missing_package",
      `Package directory ${packageDir} cannot be resolved to a real filesystem path`,
    );
  }
  const realRel = path.relative(trustedRepoReal, realPackage);
  if (realRel === "" || relativePathEscapesRoot(realRel)) {
    return refuse(
      "missing_package",
      `Package directory ${packageDir} resolves to ${realPackage}, outside trusted repo`,
    );
  }
  const lexRel = path.relative(trustedRepoLex, packageDir);
  if (filesystemTextIdentity(lexRel) !== filesystemTextIdentity(realRel)) {
    return refuse(
      "missing_package",
      `Package directory ${packageDir} has a symlink ancestor rerouting it to ${realPackage}`,
    );
  }

  // Descriptor must exist and be a JSON object.
  const { descriptor, descriptorPath } = readPackageDescriptor(packageDir);
  if (!descriptor) {
    return refuse("missing_package", `Package descriptor is missing or invalid at ${descriptorPath}`);
  }
  const sourceKind = String(descriptor.sourceKind || "").trim().toLowerCase();
  if (sourceKind !== "python" && sourceKind !== "step") {
    return refuse(
      "missing_package",
      `Package descriptor at ${descriptorPath} has unsupported sourceKind ${JSON.stringify(descriptor.sourceKind)}`,
    );
  }

  // Entry file: python source must exist as a real regular file
  // under the trusted root and be the ``.py`` the package is keyed
  // by. An imported STEP package's entry is the .step file itself.
  let entryLstat;
  try {
    entryLstat = fs.lstatSync(entryPath);
  } catch {
    return refuse("missing_package", `Package entry file ${entryPath} is missing`);
  }
  if (entryLstat.isSymbolicLink()) {
    return refuse(
      "missing_package",
      `Package entry ${entryPath} is a symbolic link; refusing to follow`,
    );
  }
  if (!entryLstat.isFile()) {
    return refuse("missing_package", `Package entry ${entryPath} is not a regular file`);
  }
  const entryReal = _safeRealpath(entryPath);
  if (!entryReal) {
    return refuse("missing_package", `Package entry ${entryPath} cannot be resolved`);
  }
  const entryRealRel = path.relative(trustedRepoReal, entryReal);
  if (entryRealRel === "" || relativePathEscapesRoot(entryRealRel)) {
    return refuse(
      "missing_package",
      `Package entry ${entryPath} resolves outside trusted repo (${entryReal})`,
    );
  }
  const entryLexRel = path.relative(trustedRepoLex, entryPath);
  if (filesystemTextIdentity(entryLexRel) !== filesystemTextIdentity(entryRealRel)) {
    return refuse(
      "missing_package",
      `Package entry ${entryPath} has a symlink ancestor rerouting it to ${entryReal}`,
    );
  }

  // ``stepPath`` present and safe.
  const stepRel = String(descriptor.stepPath || "").trim();
  if (!stepRel || stepRel.includes("\0")) {
    return refuse("missing_package", `Package descriptor at ${descriptorPath} has no valid stepPath`);
  }
  if (path.isAbsolute(stepRel) || /^[A-Za-z]:/.test(stepRel) || stepRel.startsWith("\\\\")) {
    return refuse("missing_package", `Package descriptor at ${descriptorPath} names an absolute stepPath`);
  }
  const entryDir = path.dirname(entryPath);
  const logicalStepPath = path.resolve(entryDir, stepRel);
  const logicalStepLexRel = path.relative(trustedRepoLex, logicalStepPath);
  if (logicalStepLexRel === "" || relativePathEscapesRoot(logicalStepLexRel)) {
    return refuse(
      "missing_package",
      `Package descriptor at ${descriptorPath} names a stepPath outside trusted repo`,
    );
  }
  // Reject in-repo symlink reroutes: lexical == real relative for
  // the deepest existing ancestor.
  if (!_ancestorLexEqualsRealUnderRoot({
    target: logicalStepPath,
    trustedRepoLex,
    trustedRepoReal,
  })) {
    return refuse(
      "missing_package",
      `Package descriptor at ${descriptorPath} names a stepPath whose ancestor is a symbolic link; refusing to reroute`,
    );
  }

  if (sourceKind === "python") {
    const declaredSourceRel = String(descriptor.sourcePath || "").trim();
    if (!declaredSourceRel || declaredSourceRel.includes("\0")) {
      return refuse(
        "missing_source_hash",
        `Package descriptor at ${descriptorPath} has no sourcePath binding for python entry ${entryPath}`,
      );
    }
    if (path.isAbsolute(declaredSourceRel) || /^[A-Za-z]:/.test(declaredSourceRel) || declaredSourceRel.startsWith("\\\\")) {
      return refuse(
        "missing_package",
        `Package descriptor at ${descriptorPath} names an absolute sourcePath`,
      );
    }
    const stepDir = path.dirname(logicalStepPath);
    const declaredSourceResolved = path.resolve(stepDir, declaredSourceRel);
    if (filesystemPathIdentity(declaredSourceResolved) !== filesystemPathIdentity(entryPath)) {
      return refuse(
        "missing_package",
        `Package descriptor sourcePath ${declaredSourceRel} does not resolve to entry ${entryPath}`,
      );
    }
    if (!identityOnly) {
      const declaredSourceHash = String(descriptor.sourceHash || "").trim();
      if (!declaredSourceHash) {
        return refuse(
          "missing_source_hash",
          `Package descriptor at ${descriptorPath} has no sourceHash for python entry ${entryPath}`,
        );
      }
      const currentSourceHash = sha256File(entryPath);
      if (declaredSourceHash !== currentSourceHash) {
        return {
          ok: false,
          code: "stale_step_artifact",
          reason: `python entry ${entryPath} sourceHash mismatch`,
          details: {
            sourceKind: "python",
            artifactHash: declaredSourceHash,
            currentHash: currentSourceHash,
            manifestSourcePath: declaredSourceRel,
          },
        };
      }
      const closureHash = String(descriptor.sourceClosureHash || "").trim();
      const closureFiles = descriptor.sourceClosureFiles;
      const closureByteHashes = descriptor.sourceClosureByteHashes;
      if (
        !closureHash ||
        !Array.isArray(closureFiles) ||
        closureFiles.length === 0 ||
        !closureByteHashes ||
        typeof closureByteHashes !== "object" ||
        Array.isArray(closureByteHashes)
      ) {
        return refuse(
          "missing_source_closure",
          `Package descriptor at ${descriptorPath} has no verifiable Python source closure`,
        );
      }
      const seenClosureFiles = new Set();
      for (const value of closureFiles) {
        const relativeFile = String(value || "").trim();
        if (
          !relativeFile ||
          relativeFile.includes("\0") ||
          seenClosureFiles.has(relativeFile) ||
          path.isAbsolute(relativeFile) ||
          /^[A-Za-z]:/.test(relativeFile) ||
          relativeFile.startsWith("\\\\")
        ) {
          return refuse(
            "missing_source_closure",
            `Package descriptor at ${descriptorPath} has an invalid source-closure path`,
          );
        }
        seenClosureFiles.add(relativeFile);
        const expectedByteHash = String(closureByteHashes[relativeFile] || "").trim();
        if (!/^[0-9a-f]{64}$/i.test(expectedByteHash)) {
          return refuse(
            "missing_source_closure",
            `Package descriptor at ${descriptorPath} has no byte identity for ${relativeFile}`,
          );
        }
        const closurePath = path.resolve(stepDir, relativeFile);
        const closureRel = path.relative(trustedRepoLex, closurePath);
        if (
          closureRel === "" ||
          relativePathEscapesRoot(closureRel) ||
          !_ancestorLexEqualsRealUnderRoot({
            target: closurePath,
            trustedRepoLex,
            trustedRepoReal,
          }) ||
          !fileStats(closurePath)
        ) {
          return refuse(
            "stale_source_closure",
            `Python source-closure file ${relativeFile} is missing or untrusted`,
          );
        }
        const currentByteHash = sha256File(closurePath);
        if (currentByteHash !== expectedByteHash) {
          return refuse(
            "stale_source_closure",
            `Python source-closure file ${relativeFile} changed since the package was built`,
          );
        }
      }
      if (Object.keys(closureByteHashes).length !== seenClosureFiles.size) {
        return refuse(
          "missing_source_closure",
          `Package descriptor at ${descriptorPath} has mismatched source-closure identities`,
        );
      }
    }
  } else {
    // Imported STEP package: the entry IS the STEP file.
    if (filesystemPathIdentity(entryPath) !== filesystemPathIdentity(logicalStepPath)) {
      return refuse(
        "missing_package",
        `Imported STEP package entry ${entryPath} does not equal descriptor stepPath ${logicalStepPath}`,
      );
    }
  }

  if (identityOnly) {
    return {
      ok: true,
      binding: {
        entryPath,
        packageDir,
        descriptor,
        descriptorPath,
        components: [],
        logicalStepPath,
        sourceKind,
      },
    };
  }

  if (descriptor.packageSchemaVersion !== STEP_PACKAGE_SCHEMA_VERSION) {
    return refuse(
      "unsupported_package_schema",
      `Package descriptor at ${descriptorPath} has packageSchemaVersion ${JSON.stringify(descriptor.packageSchemaVersion)}; expected ${STEP_PACKAGE_SCHEMA_VERSION}`,
    );
  }

  // Component containment.
  const componentsRaw = descriptor.components;
  if (!componentsRaw || typeof componentsRaw !== "object") {
    return refuse("missing_package", `Package descriptor at ${descriptorPath} has no components`);
  }
  const components = [];
  for (const [componentId, componentEntry] of Object.entries(componentsRaw)) {
    if (!componentEntry || typeof componentEntry !== "object") {
      return refuse(
        "missing_package",
        `Package descriptor at ${descriptorPath} has an invalid component entry for ${componentId}`,
      );
    }
    const glbRel = String(componentEntry.glb || "").trim();
    if (!glbRel || glbRel.includes("\0")) {
      return refuse(
        "missing_package",
        `Package descriptor at ${descriptorPath} component ${componentId} has no glb path`,
      );
    }
    if (path.isAbsolute(glbRel) || /^[A-Za-z]:/.test(glbRel) || glbRel.startsWith("\\\\")) {
      return refuse(
        "missing_package",
        `Package descriptor at ${descriptorPath} component ${componentId} names an absolute glb path`,
      );
    }
    const glbLexical = path.resolve(packageDir, glbRel);
    const glbLexRel = path.relative(packageDir, glbLexical);
    if (glbLexRel === "" || relativePathEscapesRoot(glbLexRel)) {
      return refuse(
        "missing_package",
        `Package descriptor references component ${componentId} outside its package directory`,
      );
    }
    let glbLstat;
    try {
      glbLstat = fs.lstatSync(glbLexical);
    } catch {
      return refuse(
        "missing_glb",
        `Package component ${componentId} GLB is missing at ${glbLexical}`,
      );
    }
    if (glbLstat.isSymbolicLink()) {
      return refuse(
        "missing_package",
        `Component ${componentId} at ${glbLexical} is a symbolic link; refusing to follow`,
      );
    }
    if (!glbLstat.isFile()) {
      return refuse(
        "missing_glb",
        `Component ${componentId} at ${glbLexical} is not a regular file`,
      );
    }
    const realGlb = _safeRealpath(glbLexical);
    if (!realGlb) {
      return refuse("missing_glb", `Component ${componentId} at ${glbLexical} cannot be resolved`);
    }
    const glbRealRel = path.relative(realPackage, realGlb);
    if (glbRealRel === "" || relativePathEscapesRoot(glbRealRel)) {
      return refuse(
        "missing_package",
        `Component ${componentId} resolves to ${realGlb}, outside package realpath ${realPackage}`,
      );
    }
    if (glbLexRel !== glbRealRel) {
      return refuse(
        "missing_package",
        `Component ${componentId} at ${glbLexical} has a symlink ancestor rerouting it`,
      );
    }
    components.push({ id: componentId, glbPath: glbLexical });
  }
  if (components.length === 0) {
    return refuse("missing_package", `Package descriptor at ${descriptorPath} has no components`);
  }

  return {
    ok: true,
    binding: {
      entryPath,
      packageDir,
      descriptor,
      descriptorPath,
      components,
      logicalStepPath,
      sourceKind,
    },
  };
}


function _bindPackageToEntry(args) {
  const detailed = _bindPackageToEntryDetailed(args);
  return detailed.ok ? detailed.binding : null;
}


function _logicalStepPathForGenerator(pythonPath, { trustedRoot } = {}) {
  // Authoritative mapping: read the cadgen render-package descriptor
  // for THIS generator entry and use its recorded ``stepPath``, which
  // cadgen writes relative to the generator source's directory
  // (see ``_assembly_provenance_manifest`` in
  // ``packages/cadgen/src/cadgen/_internal/generation.py``). Same-
  // stem builds record just the basename; explicit non-same-stem
  // builds record traversal like ``../generated/robot.step``. Fall
  // back to same-stem sibling when the descriptor is absent OR when
  // its ``stepPath`` fails trust-root containment (lexical +
  // realpath). The trusted root MUST be supplied; without it any
  // descriptor-declared path is refused.
  const dir = path.dirname(pythonPath);
  const stem = path.basename(pythonPath, ".py");
  const fallback = path.join(dir, `${stem}.step`);
  const packageDir = path.resolve(
    dir,
    CADGEN_DIRNAME,
    CADGEN_MODELS_DIRNAME,
    `${stem}.py`,
  );
  const { descriptor } = readPackageDescriptor(packageDir);
  if (!descriptor) {
    return fallback;
  }
  if (!trustedRoot) {
    return fallback;
  }
  const stepRel = String(descriptor.stepPath || "").trim();
  if (!stepRel || stepRel.includes("\0")) {
    return fallback;
  }
  if (path.isAbsolute(stepRel) || /^[A-Za-z]:/.test(stepRel) || stepRel.startsWith("\\\\")) {
    return fallback;
  }
  const resolved = path.resolve(dir, stepRel);
  const trustedLex = path.resolve(trustedRoot);
  const lexRel = path.relative(trustedLex, resolved);
  if (lexRel === "" || relativePathEscapesRoot(lexRel)) {
    return fallback;
  }
  // Real containment for the deepest existing ancestor, and require
  // lex == real relative from the trusted root. This rejects an
  // in-repo symlink/junction reroute (e.g. ``generated -> actual``)
  // that would otherwise keep the logical identity in ``generated/*``
  // while silently publishing bytes from ``actual/*``. Descriptor is
  // reduced to fallback rather than trusted when the check fails.
  const trustedReal = _safeRealpath(trustedLex);
  if (!trustedReal) {
    return fallback;
  }
  if (!_ancestorLexEqualsRealUnderRoot({
    target: resolved,
    trustedRepoLex: trustedLex,
    trustedRepoReal: trustedReal,
  })) {
    return fallback;
  }
  return resolved;
}


function _safeRealpath(p) {
  try {
    return fs.realpathSync(p);
  } catch {
    return "";
  }
}


// Deepest existing lexical ancestor of ``target``. When ``target`` does
// not yet exist we walk up its LEXICAL parents (never following
// symlinks) until an ancestor stats OK; the returned path is the
// LEXICAL absolute path of that ancestor, so callers can compare it
// against the realpath of the same ancestor and require them to
// resolve to the same location. Refuses paths whose deepest existing
// ancestor is itself a symbolic link (that alone is already an
// ancestor reroute -- even if it points inside the trusted root).
function _deepestExistingAncestorLexical(target) {
  let current = path.resolve(target);
  for (;;) {
    let lst;
    try {
      lst = fs.lstatSync(current);
    } catch {
      lst = null;
    }
    if (lst) {
      if (lst.isSymbolicLink()) {
        return "";
      }
      return current;
    }
    const parent = path.dirname(current);
    if (parent === current) {
      return "";
    }
    current = parent;
  }
}


// Fail-closed containment: the deepest existing lexical ancestor's
// realpath MUST equal its lexical path (relative to the trusted repo).
// Even if a rerouted realpath happens to stay INSIDE ``trustedRepoReal``
// (in-repo junction ``generated -> actual``), that changes the effective
// identity of the logical path and MUST be rejected. Segment-aware
// startsWith so a valid leaf named ``..part.step`` (whose first segment
// merely begins with dots) still passes.
function _ancestorLexEqualsRealUnderRoot({
  target,
  trustedRepoLex,
  trustedRepoReal,
}) {
  const lexicalAncestor = _deepestExistingAncestorLexical(target);
  if (!lexicalAncestor) return false;
  const realAncestor = _safeRealpath(lexicalAncestor);
  if (!realAncestor) return false;
  const lexRel = path.relative(trustedRepoLex, lexicalAncestor);
  const realRel = path.relative(trustedRepoReal, realAncestor);
  const firstLex = lexRel.split(/[\\/]/, 1)[0];
  const firstReal = realRel.split(/[\\/]/, 1)[0];
  if (path.isAbsolute(lexRel) || firstLex === "..") return false;
  if (path.isAbsolute(realRel) || firstReal === "..") return false;
  // Empty relative == the trusted root itself; the target lives
  // directly under repoRoot -- both must be empty and consistent.
  if (filesystemTextIdentity(lexRel) !== filesystemTextIdentity(realRel)) return false;
  return true;
}


function _deepestExistingAncestorRealpath(target) {
  let current = target;
  for (;;) {
    const real = _safeRealpath(current);
    if (real) {
      return real;
    }
    const parent = path.dirname(current);
    if (parent === current) {
      return "";
    }
    current = parent;
  }
}


function readPackageDescriptor(packageDir) {
  const resolved = path.resolve(packageDir);
  const descriptorPath = path.join(resolved, CADGEN_PACKAGE_DESCRIPTOR);
  let raw;
  try {
    raw = fs.readFileSync(descriptorPath, "utf8");
  } catch {
    return { descriptor: null, descriptorPath };
  }
  let descriptor;
  try {
    descriptor = JSON.parse(raw);
  } catch {
    return { descriptor: null, descriptorPath };
  }
  if (!descriptor || typeof descriptor !== "object" || Array.isArray(descriptor)) {
    return { descriptor: null, descriptorPath };
  }
  return { descriptor, descriptorPath };
}

function resolveStepEntryPath(stepPath, extension, { trustedRoot } = {}) {
  // Attempt authoritative resolution via the STEP file's own
  // ``cadgen:sourcePath`` metadata, but that metadata lives inside a
  // STEP file that an attacker could write. Refuse absolute paths,
  // ``..`` traversal outside ``trustedRoot``, drive/UNC anchors,
  // null bytes, and symlink/reparse ancestors. When a ``trustedRoot``
  // is not supplied we treat all metadata as untrusted and fall back
  // to same-stem inference.
  if (trustedRoot) {
    try {
      if (fileStats(stepPath)) {
        const metadata = readTextToCadStepMetadataFile(stepPath);
        const declaredSource = String(metadata?.sourcePath || "").trim();
        if (declaredSource) {
          const resolved = resolveTrustedMetadataPath(stepPath, declaredSource, trustedRoot);
          if (resolved) {
            return resolved;
          }
        }
      }
    } catch {
      // Fall through to same-stem inference.
    }
  }
  const dir = path.dirname(stepPath);
  const stem = path.basename(stepPath, extension);
  const sameStem = path.join(dir, `${stem}.py`);
  try {
    if (fs.statSync(sameStem).isFile()) {
      return sameStem;
    }
  } catch {
    // no generator beside the STEP
  }
  return stepPath;
}


export function resolveTrustedMetadataPath(stepPath, declaredSource, trustedRoot) {
  const raw = String(declaredSource || "");
  if (!raw || raw.includes("\0")) {
    return "";
  }
  if (path.isAbsolute(raw) || /^[A-Za-z]:/.test(raw) || raw.startsWith("\\\\")) {
    return "";
  }
  const baseDir = path.resolve(path.dirname(stepPath));
  const lexical = path.resolve(baseDir, raw);
  const trustedLexical = path.resolve(trustedRoot);
  const lexRel = path.relative(trustedLexical, lexical);
  if (lexRel === "" || relativePathEscapesRoot(lexRel)) {
    return "";
  }
  let lstat;
  try {
    lstat = fs.lstatSync(lexical);
  } catch {
    return "";
  }
  if (lstat.isSymbolicLink() || !lstat.isFile()) {
    return "";
  }
  let realFile;
  let realRoot;
  try {
    realFile = fs.realpathSync(lexical);
    realRoot = fs.realpathSync(trustedLexical);
  } catch {
    return "";
  }
  const realRel = path.relative(realRoot, realFile);
  if (realRel === "" || relativePathEscapesRoot(realRel)) {
    return "";
  }
  if (lexRel !== realRel) {
    return "";
  }
  return lexical;
}


function componentRefStaysInsidePackage(packageDir, glbRelative) {
  const raw = String(glbRelative || "").trim();
  if (!raw || raw.includes("\0")) {
    return false;
  }
  if (path.isAbsolute(raw) || /^[A-Za-z]:/.test(raw) || raw.startsWith("\\\\")) {
    return false;
  }
  const resolvedPackage = path.resolve(packageDir);
  const resolved = path.resolve(resolvedPackage, raw);
  const rel = path.relative(resolvedPackage, resolved);
  return rel !== "" && rel !== "." && !relativePathEscapesRoot(rel);
}

export const VIEWER_SKIPPED_DIRECTORIES = new Set([
  ".agents",
  ".cache",
  ".viewer",
  ".git",
  ".venv",
  "__cadgen__",
  "__pycache__",
  "build",
  "coverage",
  "dist",
  "node_modules",
  "viewer",
]);
const SRDF_URDF_METADATA_PATTERN = /<\s*(?:tcad|explorer):urdf\b[^>]*\bpath\s*=\s*["']([^"']+)["'][^>]*>/i;
const STEP_EDGE_RENDER_CLASS_ORDER = Object.freeze(["feature", "tangent", "seam", "degenerate"]);
const TEXT_TO_CAD_COMMENT_METADATA_RE = /<!--\s*cadpy:([A-Za-z][A-Za-z0-9]*)=([\s\S]*?)-->/g;
const PYTHON_GENERATOR_BY_KIND = Object.freeze({
  dxf: "gen_dxf",
  step: "gen_step",
  stp: "gen_step",
  urdf: "gen_urdf",
  srdf: "gen_srdf",
  sdf: "gen_sdf",
});

function encodeUrlPath(repoRelativePath) {
  return `/${repoRelativePath.split("/").map((part) => encodeURIComponent(part)).join("/")}`;
}

function relativePathStaysInsideRoot(relativePath) {
  return relativePath === "" || (
    relativePath !== ".." &&
    !relativePath.startsWith(`..${path.sep}`) &&
    !path.isAbsolute(relativePath)
  );
}

function normalizeStepEdgeRenderVisibilityClasses(value) {
  const rawValues = Array.isArray(value) ? value : value === undefined || value === null ? [] : [value];
  const validValues = new Set(Object.values(STEP_EDGE_VISIBILITY_CLASSES));
  const normalized = [];
  for (const raw of rawValues) {
    const classId = String(raw || "").trim();
    if (validValues.has(classId) && !normalized.includes(classId)) {
      normalized.push(classId);
    }
  }
  if (!normalized.includes(STEP_EDGE_VISIBILITY_CLASSES.FEATURE)) {
    normalized.unshift(STEP_EDGE_VISIBILITY_CLASSES.FEATURE);
  }
  return [
    ...STEP_EDGE_RENDER_CLASS_ORDER.filter((classId) => normalized.includes(classId)),
    ...normalized.filter((classId) => !STEP_EDGE_RENDER_CLASS_ORDER.includes(classId))
  ];
}

export function normalizeViewerRootDir(value = DEFAULT_VIEWER_ROOT_DIR) {
  const rawValue = String(value ?? "").trim();
  const slashNormalized = rawValue.replace(/\\/g, "/");
  const normalized = path.posix.normalize(slashNormalized);
  if (!normalized || normalized === ".") {
    return DEFAULT_VIEWER_ROOT_DIR;
  }
  if (normalized === ".." || normalized.startsWith("../")) {
    throw new Error(`CAD Viewer root directory must stay inside the directory root: ${rawValue}`);
  }
  return normalized.replace(/(?!^\/)\/+$/, "");
}

export function resolveViewerRoot(repoRoot, rootDir = DEFAULT_VIEWER_ROOT_DIR) {
  const normalizedDir = normalizeViewerRootDir(rootDir);
  const resolvedRepoRoot = path.resolve(repoRoot);
  const rootPath = normalizedDir
    ? path.resolve(resolvedRepoRoot, normalizedDir)
    : resolvedRepoRoot;
  const relativePath = path.relative(resolvedRepoRoot, rootPath);
  if (!relativePathStaysInsideRoot(relativePath)) {
    throw new Error(`CAD Viewer root directory must stay inside the directory root: ${normalizedDir}`);
  }
  return {
    dir: normalizedDir,
    rootPath,
    rootName: normalizedDir ? path.basename(rootPath) : path.basename(resolvedRepoRoot),
  };
}

export function repoRelativePath(repoRoot, filePath) {
  return toPosixPath(path.relative(path.resolve(repoRoot), path.resolve(filePath)));
}

function scanRelativePath(rootPath, filePath) {
  return toPosixPath(path.relative(path.resolve(rootPath), path.resolve(filePath)));
}

function fileStats(filePath) {
  try {
    const stats = fs.statSync(filePath, { bigint: true });
    return stats.isFile() ? stats : null;
  } catch {
    return null;
  }
}

function fileVersion(filePath) {
  const stats = fileStats(filePath);
  if (!stats) {
    return "";
  }
  return `${stats.size.toString(36)}-${stats.mtimeNs.toString(36)}`;
}

function sha256File(filePath) {
  const hash = crypto.createHash("sha256");
  const fd = fs.openSync(filePath, "r");
  try {
    const buffer = Buffer.alloc(1024 * 1024);
    let bytesRead = 0;
    do {
      bytesRead = fs.readSync(fd, buffer, 0, buffer.length, null);
      if (bytesRead > 0) {
        hash.update(buffer.subarray(0, bytesRead));
      }
    } while (bytesRead > 0);
  } finally {
    fs.closeSync(fd);
  }
  return hash.digest("hex");
}

function pathIsInside(filePath, rootPath) {
  const relative = path.relative(path.resolve(rootPath), path.resolve(filePath));
  return relativePathStaysInsideRoot(relative);
}

function dedupePaths(paths) {
  const result = [];
  const seen = new Set();
  for (const rawPath of paths) {
    const resolved = path.resolve(rawPath);
    if (!seen.has(resolved)) {
      seen.add(resolved);
      result.push(resolved);
    }
  }
  return result;
}

function normalizeManifestPath(manifestPath) {
  const value = String(manifestPath || "").trim();
  if (!value || value.includes("\0")) {
    return "";
  }
  return value.replace(/\\/g, "/");
}

function resolveManifestSourcePath(repoRoot, manifestPath, baseDir = repoRoot) {
  const value = normalizeManifestPath(manifestPath);
  if (!value) {
    return null;
  }
  const resolved = path.isAbsolute(value)
    ? path.resolve(value)
    : path.resolve(baseDir, value);
  if (!pathIsInside(resolved, repoRoot)) {
    return null;
  }
  return resolved;
}

function normalizeManifestRelativePath(manifestPath) {
  const value = String(manifestPath || "").trim().replace(/\\/g, "/").replace(/^\/+/, "");
  if (!value || value === "." || value.startsWith("../")) {
    return "";
  }
  return value;
}

function manifestIdentityRootForStep(repoRoot, actualStepPath, manifestStepPath) {
  const resolvedRepoRoot = path.resolve(repoRoot);
  const normalizedStepPath = normalizeManifestRelativePath(manifestStepPath);
  if (!normalizedStepPath || !actualStepPath) {
    return resolvedRepoRoot;
  }
  const actualRepoPath = repoRelativePath(resolvedRepoRoot, actualStepPath);
  if (actualRepoPath === normalizedStepPath) {
    return resolvedRepoRoot;
  }
  if (!actualRepoPath.endsWith(`/${normalizedStepPath}`)) {
    return resolvedRepoRoot;
  }
  const prefix = actualRepoPath.slice(0, actualRepoPath.length - normalizedStepPath.length).replace(/\/+$/, "");
  if (!prefix) {
    return resolvedRepoRoot;
  }
  const resolvedPrefix = path.resolve(resolvedRepoRoot, ...prefix.split("/").filter(Boolean));
  return pathIsInside(resolvedPrefix, resolvedRepoRoot) ? resolvedPrefix : resolvedRepoRoot;
}

function sourcePathFromManifest(repoRoot, manifestPath, { identityRoot = null, baseDir = null } = {}) {
  const value = normalizeManifestPath(manifestPath);
  if (!value) {
    return { sourcePath: "", manifestSourcePath: "", filePath: null, identityRoot: path.resolve(repoRoot) };
  }
  const resolvedRepoRoot = path.resolve(repoRoot);
  const resolvedIdentityRoot = identityRoot && pathIsInside(identityRoot, resolvedRepoRoot)
    ? path.resolve(identityRoot)
    : resolvedRepoRoot;
  const roots = dedupePaths([
    baseDir ? path.resolve(baseDir) : null,
    resolvedIdentityRoot,
    resolvedRepoRoot,
  ].filter(Boolean));
  const candidates = [];
  for (const root of roots) {
    if (!pathIsInside(root, resolvedRepoRoot)) {
      continue;
    }
    const filePath = resolveManifestSourcePath(resolvedRepoRoot, value, root);
    if (!filePath) {
      continue;
    }
    candidates.push({
      sourcePath: repoRelativePath(resolvedRepoRoot, filePath),
      manifestSourcePath: value,
      filePath,
      identityRoot: resolvedIdentityRoot,
    });
  }
  const candidate = candidates.find((entry) => fileStats(entry.filePath)) || candidates[0];
  if (candidate) {
    return candidate;
  }
  const filePath = resolveManifestSourcePath(resolvedRepoRoot, value, resolvedRepoRoot);
  return {
    sourcePath: value,
    manifestSourcePath: value,
    filePath,
    identityRoot: resolvedIdentityRoot,
  };
}

function fileHasGenStep(filePath) {
  try {
    return /\bgen_step\s*\(/.test(fs.readFileSync(filePath, "utf-8"));
  } catch {
    return false;
  }
}

function generatorSourcePathFromManifest(repoRoot, manifestPath, { identityRoot = null, baseDir = null } = {}) {
  const candidate = sourcePathFromManifest(repoRoot, manifestPath, { identityRoot, baseDir });
  if (
    candidate.sourcePath &&
    candidate.filePath &&
    path.extname(candidate.filePath).toLowerCase() === ".py" &&
    path.basename(candidate.filePath) !== "__init__.py" &&
    fileHasGenStep(candidate.filePath)
  ) {
    return candidate;
  }
  return {
    sourcePath: "",
    manifestSourcePath: candidate.manifestSourcePath || "",
    filePath: null,
    identityRoot: candidate.identityRoot || path.resolve(repoRoot),
  };
}

function fileHasPythonGenerator(filePath, generatorName) {
  if (!generatorName) {
    return false;
  }
  try {
    const source = fs.readFileSync(filePath, "utf-8");
    return new RegExp(`\\b${generatorName}\\s*\\(`).test(source);
  } catch {
    return false;
  }
}

function generatorSourcePathFromMetadata(repoRoot, manifestPath, generatorName, { baseDir = null } = {}) {
  const candidate = sourcePathFromManifest(repoRoot, manifestPath, { baseDir });
  if (
    candidate.sourcePath &&
    candidate.filePath &&
    path.extname(candidate.filePath).toLowerCase() === ".py" &&
    path.basename(candidate.filePath) !== "__init__.py" &&
    fileHasPythonGenerator(candidate.filePath, generatorName)
  ) {
    return candidate;
  }
  return { sourcePath: "", filePath: null };
}

function readXmlTextToCadMetadata(filePath) {
  let text = "";
  try {
    text = fs.readFileSync(filePath, "utf-8");
  } catch {
    return {};
  }
  const metadata = {};
  for (const match of text.matchAll(TEXT_TO_CAD_COMMENT_METADATA_RE)) {
    metadata[String(match[1] || "").trim()] = String(match[2] || "").trim();
  }
  return metadata;
}

function readDxfTextToCadMetadata(filePath) {
  let lines = [];
  try {
    lines = fs.readFileSync(filePath, "utf-8").split(/\r?\n/);
  } catch {
    return {};
  }
  const metadata = {};
  for (let index = 0; index + 1 < lines.length; index += 1) {
    if (String(lines[index] || "").trim() !== "999") {
      continue;
    }
    const value = String(lines[index + 1] || "").trim();
    if (!value.startsWith("cadpy:")) {
      continue;
    }
    const [key, ...rest] = value.slice("cadpy:".length).split("=");
    const normalizedKey = String(key || "").trim();
    if (rest.length && /^[A-Za-z][A-Za-z0-9]*$/.test(normalizedKey)) {
      metadata[normalizedKey] = rest.join("=").trim();
    }
  }
  return metadata;
}

function readGeneratedFileMetadata(filePath, kind) {
  const normalizedKind = String(kind || "").trim().toLowerCase();
  if (normalizedKind === "dxf") {
    return readDxfTextToCadMetadata(filePath);
  }
  if (["urdf", "srdf", "sdf"].includes(normalizedKind)) {
    return readXmlTextToCadMetadata(filePath);
  }
  if (normalizedKind === "step" || normalizedKind === "stp") {
    try {
      return readTextToCadStepMetadataFile(filePath);
    } catch {
      return {};
    }
  }
  return {};
}

function generatedSourceStatusForFile({ repoRoot, sourcePath, kind }) {
  const normalizedKind = String(kind || "").trim().toLowerCase();
  const generatorName = PYTHON_GENERATOR_BY_KIND[normalizedKind] || "";
  if (!generatorName) {
    return null;
  }
  const metadata = readGeneratedFileMetadata(sourcePath, normalizedKind);
  const metadataSourcePath = String(metadata.sourcePath || "").trim();
  if (!metadataSourcePath) {
    return null;
  }
  const sourceIdentity = generatorSourcePathFromMetadata(
    repoRoot,
    metadataSourcePath,
    generatorName,
    { baseDir: path.dirname(sourcePath) },
  );
  const base = {
    sourceKind: "python",
    source: {
      file: sourceIdentity.sourcePath || metadataSourcePath,
      sourcePath: sourceIdentity.sourcePath || metadataSourcePath,
      ...(metadata.sourceHash ? { sourceHash: String(metadata.sourceHash) } : {}),
    },
  };
  if (!sourceIdentity.filePath) {
    return {
      ...base,
      sourceStatus: {
        ok: false,
        status: "missing",
        stale: false,
        sourceKind: "python",
        sourcePath: metadataSourcePath,
        message: "Python generator source is unavailable.",
      },
    };
  }
  return {
    ...base,
    source: {
      ...base.source,
      file: sourceIdentity.sourcePath,
      sourcePath: sourceIdentity.sourcePath,
      sourceHash: String(metadata.sourceHash || ""),
    },
  };
}

function assetForPath(repoRoot, filePath) {
  const stats = fileStats(filePath);
  if (!stats) {
    // Package directory: version + hash come from the descriptor.
    // Mirrors viewer/server_py/scanner.py::asset_for_path.
    const descriptorPath = path.join(String(filePath || ""), CADGEN_PACKAGE_DESCRIPTOR);
    const descriptorStats = fileStats(descriptorPath);
    if (descriptorStats) {
      const version = `${descriptorStats.size.toString(36)}-${descriptorStats.mtimeNs.toString(36)}`;
      const repoPath = repoRelativePath(repoRoot, filePath);
      return {
        url: `${encodeUrlPath(repoPath)}?v=${encodeURIComponent(version)}`,
        hash: sha256File(descriptorPath),
        bytes: Number(descriptorStats.size),
      };
    }
    return null;
  }
  const version = `${stats.size.toString(36)}-${stats.mtimeNs.toString(36)}`;
  const repoPath = repoRelativePath(repoRoot, filePath);
  return {
    url: `${encodeUrlPath(repoPath)}?v=${encodeURIComponent(version)}`,
    hash: sha256File(filePath),
    bytes: Number(stats.size),
  };
}

function assetUrlForPath(repoRoot, filePath) {
  return encodeUrlPath(repoRelativePath(repoRoot, filePath));
}

function readExact(fd, length, position) {
  const buffer = Buffer.alloc(length);
  const bytesRead = fs.readSync(fd, buffer, 0, length, position);
  return bytesRead === length ? buffer : null;
}

function glbBufferViewRange(gltf, binOffset, binLength, viewIndex) {
  const view = Array.isArray(gltf?.bufferViews) ? gltf.bufferViews[Number(viewIndex)] : null;
  if (!view || Number(view.buffer || 0) !== 0) {
    return null;
  }
  const byteOffset = binOffset + Number(view.byteOffset || 0);
  const byteLength = Number(view.byteLength || 0);
  if (!Number.isFinite(byteOffset) || !Number.isFinite(byteLength) || byteLength < 0) {
    return null;
  }
  if (byteOffset < binOffset || byteOffset + byteLength > binOffset + binLength) {
    return null;
  }
  return { byteOffset, byteLength };
}

function parseJsonBufferView(fd, gltf, binOffset, binLength, viewIndex, encoding = "utf-8") {
  const range = glbBufferViewRange(gltf, binOffset, binLength, viewIndex);
  if (!range) {
    throw new Error("STEP topology buffer view range is invalid");
  }
  const bytes = readExact(fd, range.byteLength, range.byteOffset);
  if (!bytes) {
    throw new Error("STEP topology buffer view range is invalid");
  }
  const payload = JSON.parse(bytes.toString(String(encoding || "utf-8")));
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error("STEP topology JSON buffer view is not an object");
  }
  return payload;
}

function gltfPrimitivesHaveSurfaceEdgeAttributes(gltf) {
  const meshes = Array.isArray(gltf?.meshes) ? gltf.meshes : [];
  let primitiveCount = 0;
  for (const mesh of meshes) {
    for (const primitive of Array.isArray(mesh?.primitives) ? mesh.primitives : []) {
      primitiveCount += 1;
      const attributes = primitive?.attributes || {};
      if (
        attributes[STEP_EDGE_BARYCENTRIC_ATTRIBUTE] === undefined ||
        attributes[STEP_EDGE_CLASS_ATTRIBUTE] === undefined
      ) {
        return false;
      }
    }
  }
  return primitiveCount > 0;
}

function readGlbTopologyContainer(filePath) {
  let fd = null;
  try {
    fd = fs.openSync(filePath, "r");
    const header = readExact(fd, 12, 0);
    if (!header || header.readUInt32LE(0) !== 0x46546c67 || header.readUInt32LE(4) !== 2) {
      throw new Error("Not a GLB v2 file");
    }
    const totalLength = Math.min(header.readUInt32LE(8), fs.fstatSync(fd).size);
    let offset = 12;
    let gltf = null;
    let binOffset = 0;
    let binLength = 0;
    while (offset + 8 <= totalLength) {
      const chunkHeader = readExact(fd, 8, offset);
      if (!chunkHeader) {
        throw new Error("Invalid GLB chunk header");
      }
      const chunkLength = chunkHeader.readUInt32LE(0);
      const chunkType = chunkHeader.toString("latin1", 4, 8);
      offset += 8;
      if (offset + chunkLength > totalLength) {
        throw new Error("Invalid GLB chunk length");
      }
      if (chunkType === "JSON") {
        const jsonBytes = readExact(fd, chunkLength, offset);
        if (!jsonBytes) {
          throw new Error("GLB is missing JSON chunk");
        }
        gltf = JSON.parse(jsonBytes.toString("utf8").trim());
      } else if (chunkType === "BIN\u0000") {
        binOffset = offset;
        binLength = chunkLength;
      }
      offset += chunkLength;
    }
    return {
      fd,
      gltf,
      binOffset,
      binLength,
    };
  } catch {
    if (fd !== null) {
      fs.closeSync(fd);
    }
    throw new Error("Invalid GLB topology container");
  }
}

function stepArtifactError({ code, reason, repoRoot, cadPath, sourcePath, glbPath, details = {} }) {
  const glbRelPath = repoRelativePath(repoRoot, glbPath);
  return {
    ok: false,
    error: {
      code,
      message: `${reason}: ${glbRelPath}.`,
      cadPath,
      stepPath: repoRelativePath(repoRoot, sourcePath),
      glbPath: glbRelPath,
      regenerateCommand: REGENERATE_STEP_COMMAND,
      ...details,
    },
  };
}

function staleStepArtifactError({
  repoRoot,
  cadPath,
  sourcePath,
  glbPath,
  manifestSourcePath = "",
  sourceKind = "step",
  artifactHash,
  currentHash,
}) {
  return stepArtifactError({
    code: "stale_step_artifact",
    reason: "Generated GLB doesn't match the hash of the STEP file",
    repoRoot,
    cadPath,
    sourcePath,
    glbPath,
    details: {
      stale: true,
      sourceKind,
      ...(manifestSourcePath ? { sourcePath: manifestSourcePath } : {}),
      artifactHash,
      currentHash,
    },
  });
}

export function validateStepTopologyArtifact({ repoRoot, sourcePath, cadPath, entryPath = "" }) {
  const glbPath = inlineStepGlbArtifactPathForSource(sourcePath);
  // ``entryPath`` names the cadgen entry file: the ``.step.py`` source
  // for a generated model, or the ``.step``/``.stp`` file itself for
  // an imported one. When callers do not name it we fall back to the
  // STEP path -- which is correct for imported models -- so the check
  // still runs. Generated-model callers (the compiler) always pass
  // their generator source so the check binds to the canonical
  // ``__cadgen__/models/<generator>`` package.
  const resolvedEntry = entryPath ? path.resolve(entryPath) : path.resolve(sourcePath);
  const packageDir = packageDirForEntry(resolvedEntry);
  const packageFail = (code, reason, details = {}) => ({
    topology: null,
    stepArtifact: stepArtifactError({
      code,
      reason,
      repoRoot,
      cadPath,
      sourcePath,
      glbPath,
      details: {
        packageDir: repoRelativePath(repoRoot, packageDir),
        ...details,
      },
    }),
    glbPath,
    packageDir,
    stepHash: "",
    sourceHash: "",
  });

  // Read the descriptor only to decide whether "there is a candidate
  // package to accept". Every acceptance check -- canonical location,
  // symlink/reparse containment, in-repo reroute, sourceKind bounds,
  // sourcePath binding, sourceHash identity, component containment --
  // is delegated to ``bindCadgenPackage``. This ELIMINATES the older
  // duplicated acceptance logic that let a python descriptor without
  // ``sourcePath``/``sourceHash`` pass silently.
  const { descriptor } = readPackageDescriptor(packageDir);
  if (descriptor) {
    const bindResult = bindCadgenPackageDetailed({
      packageDir,
      trustedRoot: repoRoot,
      entryPath: resolvedEntry,
    });
    if (!bindResult.ok) {
      // Route the binder's specific rejection code (missing_package,
      // missing_glb, missing_source_hash, stale_step_artifact) into
      // the standard stepArtifactError shape. This is the ONLY
      // acceptance path -- no duplicated python/imported checks below.
      if (bindResult.code === "stale_step_artifact") {
        const d = bindResult.details || {};
        return {
          topology: null,
          stepArtifact: staleStepArtifactError({
            repoRoot,
            cadPath,
            sourcePath,
            glbPath,
            manifestSourcePath: d.manifestSourcePath || "",
            sourceKind: d.sourceKind || "python",
            artifactHash: d.artifactHash || "",
            currentHash: d.currentHash || "",
          }),
          glbPath,
          packageDir,
          stepHash: d.artifactHash || "",
          sourceHash: d.artifactHash || "",
        };
      }
      return packageFail(bindResult.code || "missing_package", bindResult.reason || "package binding refused");
    }
    const binding = bindResult.binding;
    // Freshness: the binder has already verified structural identity
    // (entry file, sourceHash matches current generator, sourcePath
    // resolves exactly, canonical location, components inside). Layer
    // stepHash freshness against the on-disk STEP file here so the
    // scanner still catches "STEP file edited but package not
    // rebuilt".
    const artifactSourceKind = binding.sourceKind;
    const artifactUsesPython = artifactSourceKind === "python";
    const stepHash = String(binding.descriptor.stepHash || "").trim();
    const sourceHash = String(binding.descriptor.sourceHash || "").trim();
    const stepFileExists = Boolean(fileStats(sourcePath));
    const currentStepHash = stepFileExists ? sha256File(sourcePath) : "";
    if (!/^[0-9a-f]{64}$/i.test(stepHash)) {
      return packageFail(
        "missing_step_hash",
        `Package descriptor at ${binding.descriptorPath} has no valid stepHash binding for ${sourcePath}`,
      );
    }
    if (stepFileExists) {
      if (stepHash !== currentStepHash) {
        return {
          topology: null,
          stepArtifact: staleStepArtifactError({
            repoRoot,
            cadPath,
            sourcePath,
            glbPath,
            manifestSourcePath: String(binding.descriptor.sourcePath || ""),
            sourceKind: artifactUsesPython ? "python" : "step",
            artifactHash: stepHash,
            currentHash: currentStepHash,
          }),
          glbPath,
          packageDir,
          stepHash,
          sourceHash,
        };
      }
    }
    const topology = {
      index: binding.descriptor,
      entryKind: String(binding.descriptor.entryKind || "").trim().toLowerCase(),
      hasSelector: false,
      hasDisplayEdges: true,
    };
    return {
      topology,
      stepArtifact: {
        ok: true,
        sourceKind: artifactUsesPython ? "python" : "step",
        sourcePath: String(binding.descriptor.sourcePath || ""),
      },
      glbPath,
      packageDir,
      stepHash,
      sourceHash,
    };
  }

  // No canonical package on disk. Preserve the legacy inline-GLB
  // fallback below so pre-cadgen fixtures still produce a
  // ``missing_glb`` diagnostic (there is no cadgen build to reuse).
  let stepHash = "";
  let sourceHash = "";
  let artifactSourcePath = "";
  const fail = (code, reason) => ({
    topology: null,
    stepArtifact: stepArtifactError({ code, reason, repoRoot, cadPath, sourcePath, glbPath }),
    glbPath,
    packageDir,
    stepHash,
    sourceHash,
  });

  if (!fileStats(glbPath)) {
    return fail(
      "missing_glb",
      "STEP topology validation requires the generated GLB artifact, but it is missing"
    );
  }

  let container = null;
  try {
    container = readGlbTopologyContainer(glbPath);
    const extension = container.gltf?.extensions?.[STEP_TOPOLOGY_EXTENSION];
    if (!extension || typeof extension !== "object" || Array.isArray(extension)) {
      return fail(
        "missing_step_topology",
        "STEP topology validation requires readable STEP_topology indexView in the GLB"
      );
    }
    if (!isCurrentStepTopologySchemaVersion(extension.schemaVersion)) {
      return fail(
        "unsupported_step_topology",
        `STEP topology validation requires STEP_topology schemaVersion ${STEP_TOPOLOGY_SCHEMA_VERSION} in the GLB`
      );
    }
    const manifest = parseJsonBufferView(
      container.fd,
      container.gltf,
      container.binOffset,
      container.binLength,
      extension.indexView,
      extension.encoding
    );
    // Legacy inline-GLB path -- only reached when there is no cadgen
    // package on disk. The identity checks below run against the GLB's
    // own STEP_topology manifest, matching pre-cadgen fixtures.
    const topology = {
      index: manifest,
      entryKind: String(extension.entryKind || manifest.entryKind || "").trim().toLowerCase(),
      hasSelector: false,
      hasDisplayEdges: false,
    };
    if (!isCurrentStepTopologySchemaVersion(manifest.schemaVersion)) {
      return {
        topology,
        stepArtifact: stepArtifactError({
          code: "unsupported_step_topology",
          reason: `STEP topology validation requires STEP_topology schemaVersion ${STEP_TOPOLOGY_SCHEMA_VERSION} in the GLB`,
          repoRoot,
          cadPath,
          sourcePath,
          glbPath,
        }),
        glbPath,
        stepHash,
      };
    }
    const artifactSourceKind = String(manifest.sourceKind || "step").trim().toLowerCase();
    const artifactUsesPythonSource = artifactSourceKind === "python";
    const artifactNormalizedSourceKind = artifactUsesPythonSource ? "python" : "step";
    const artifactIdentityDetails = () => ({
      sourceKind: artifactNormalizedSourceKind,
      ...(artifactSourcePath ? { sourcePath: artifactSourcePath } : {}),
    });
    const manifestIdentityRoot = manifestIdentityRootForStep(repoRoot, sourcePath, manifest.stepPath);
    const sourceIdentity = artifactUsesPythonSource
      ? generatorSourcePathFromManifest(repoRoot, manifest.sourcePath, {
          identityRoot: manifestIdentityRoot,
          baseDir: path.dirname(glbPath),
        })
      : sourcePathFromManifest(repoRoot, manifest.sourcePath, {
          identityRoot: manifestIdentityRoot,
          baseDir: path.dirname(glbPath),
        });
    artifactSourcePath = sourceIdentity.sourcePath;
    if (!artifactSourcePath || !sourceIdentity.filePath) {
      return {
        topology,
        stepArtifact: stepArtifactError({
          code: "missing_source_path",
          reason: "GLB STEP_topology is missing required sourcePath identity",
          repoRoot,
          cadPath,
          sourcePath,
          glbPath,
          details: {
            sourceKind: artifactNormalizedSourceKind,
          },
        }),
        glbPath,
        stepHash,
        sourceHash,
      };
    }
    stepHash = String(manifest.stepHash || "").trim();
    sourceHash = String(manifest.sourceHash || "").trim();
    if (!stepHash && fileStats(sourcePath)) {
      return {
        topology,
        stepArtifact: stepArtifactError({
          code: "missing_step_hash",
          reason: "GLB STEP_topology is missing STEP file identity",
          repoRoot,
          cadPath,
          sourcePath,
          glbPath,
          details: artifactIdentityDetails(),
        }),
        glbPath,
        stepHash,
        sourceHash,
      };
    }
    const currentStepHash = fileStats(sourcePath) ? sha256File(sourcePath) : "";
    if (currentStepHash && stepHash !== currentStepHash) {
      return {
        topology,
        stepArtifact: staleStepArtifactError({
          repoRoot,
          cadPath,
          sourcePath,
          glbPath,
          manifestSourcePath: artifactSourcePath,
          sourceKind: artifactNormalizedSourceKind,
          artifactHash: stepHash,
          currentHash: currentStepHash,
        }),
        glbPath,
        stepHash,
        sourceHash,
      };
    }
    let edgeRendering = null;
    try {
      const edgeManifest = parseJsonBufferView(
        container.fd,
        container.gltf,
        container.binOffset,
        container.binLength,
        extension.edgeView,
        extension.encoding
      );
      const edgeVisibilityClasses = edgeManifest?.edgeRendering && typeof edgeManifest.edgeRendering === "object"
        ? normalizeStepEdgeRenderVisibilityClasses(edgeManifest.edgeRendering.visibilityClasses)
        : [];
      const indexEdgeVisibilityClasses = manifest?.edgeRendering && typeof manifest.edgeRendering === "object"
        ? normalizeStepEdgeRenderVisibilityClasses(manifest.edgeRendering.visibilityClasses)
        : [];
      if (
        !isCurrentStepTopologySchemaVersion(edgeManifest.schemaVersion) ||
        String(edgeManifest.profile || "") !== "surface-edges" ||
        String(edgeManifest.sourcePath || "").trim() !== String(manifest.sourcePath || "").trim() ||
        (stepHash && String(edgeManifest.stepHash || "").trim() !== stepHash) ||
        !edgeVisibilityClasses.length ||
        edgeVisibilityClasses.join("\n") !== indexEdgeVisibilityClasses.join("\n") ||
        !edgeVisibilityClasses.includes(STEP_EDGE_VISIBILITY_CLASSES.FEATURE) ||
        !edgeManifest?.buffers?.views?.surfaceHalfEdges
      ) {
        return {
          topology,
          stepArtifact: stepArtifactError({
            code: "missing_edge_topology",
          reason: `STEP topology validation requires STEP_topology edgeView schemaVersion ${STEP_TOPOLOGY_SCHEMA_VERSION} in the GLB`,
          repoRoot,
          cadPath,
          sourcePath,
          glbPath,
          details: artifactIdentityDetails(),
        }),
        glbPath,
        stepHash,
        sourceHash,
        };
      }
      edgeRendering = edgeManifest.edgeRendering && typeof edgeManifest.edgeRendering === "object"
        ? {
            visibilityClasses: edgeVisibilityClasses,
            generatedVisibilityClasses: (Array.isArray(edgeManifest.edgeRendering.generatedVisibilityClasses)
              ? edgeManifest.edgeRendering.generatedVisibilityClasses
              : edgeVisibilityClasses
            )
              .map((classId) => String(classId || "").trim())
              .filter((classId, index, list) => (
                edgeVisibilityClasses.includes(classId) &&
                STEP_EDGE_RENDER_CLASS_ORDER.includes(classId) &&
                list.indexOf(classId) === index
              )),
            visibilityClassCounts: edgeManifest.edgeRendering.visibilityClassCounts || {},
            generatedVisibilityClassCounts: edgeManifest.edgeRendering.generatedVisibilityClassCounts || {},
          }
        : null;
      if (!gltfPrimitivesHaveSurfaceEdgeAttributes(container.gltf)) {
        return {
          topology,
          stepArtifact: stepArtifactError({
            code: "missing_surface_edge_attributes",
            reason: `STEP topology validation requires ${STEP_EDGE_BARYCENTRIC_ATTRIBUTE} and ${STEP_EDGE_CLASS_ATTRIBUTE} on every STEP mesh primitive`,
            repoRoot,
            cadPath,
            sourcePath,
            glbPath,
            details: artifactIdentityDetails(),
          }),
          glbPath,
          stepHash,
          sourceHash,
        };
      }
    } catch {
      return {
        topology,
        stepArtifact: stepArtifactError({
          code: "missing_edge_topology",
          reason: "STEP topology validation requires readable STEP_topology edgeView in the GLB",
          repoRoot,
          cadPath,
          sourcePath,
          glbPath,
          details: artifactIdentityDetails(),
        }),
        glbPath,
        stepHash,
        sourceHash,
      };
    }
    topology.hasDisplayEdges = true;
    try {
      const selectorManifest = parseJsonBufferView(
        container.fd,
        container.gltf,
        container.binOffset,
        container.binLength,
        extension.selectorView,
        extension.encoding
      );
      if (!isCurrentStepTopologySchemaVersion(selectorManifest.schemaVersion)) {
        return {
          topology,
          stepArtifact: stepArtifactError({
            code: "unsupported_step_topology",
            reason: `STEP topology validation requires STEP_topology schemaVersion ${STEP_TOPOLOGY_SCHEMA_VERSION} in the GLB`,
            repoRoot,
            cadPath,
            sourcePath,
            glbPath,
            details: artifactIdentityDetails(),
          }),
          glbPath,
          stepHash,
          sourceHash,
        };
      }
    } catch {
      return {
        topology,
        stepArtifact: stepArtifactError({
          code: "missing_selector_topology",
          reason: "STEP topology validation requires readable STEP_topology selectorView in the GLB",
          repoRoot,
          cadPath,
          sourcePath,
          glbPath,
          details: artifactIdentityDetails(),
        }),
        glbPath,
        stepHash,
        sourceHash,
        };
      }
    topology.hasSelector = true;
    const stepArtifact = {
      ok: true,
      glbPath: repoRelativePath(repoRoot, glbPath),
      ...(artifactSourcePath ? { sourcePath: artifactSourcePath } : {}),
      sourceKind: artifactUsesPythonSource ? "python" : "step",
      ...(edgeRendering ? { edgeRendering } : {}),
      ...(artifactUsesPythonSource
        ? {
            sourceHash,
            ...(stepHash ? { stepHash } : {}),
          }
        : {
            stepHash,
          }),
    };
    return {
      topology,
      stepArtifact,
      glbPath,
      stepHash,
      sourceHash,
    };
  } catch {
    return fail(
      "missing_step_topology",
      "STEP topology validation requires readable STEP_topology indexView in the GLB"
    );
  } finally {
    if (container?.fd !== null && container?.fd !== undefined) {
      try {
        fs.closeSync(container.fd);
      } catch {
        // Ignore close failures during catalog scanning.
      }
    }
  }
}

export function readStepSourceStatus({
  repoRoot,
  stepPath,
  pythonSourcePath = "",
  cadPath = "",
} = {}) {
  if (!repoRoot) {
    throw new Error("repoRoot is required");
  }
  if (!stepPath) {
    throw new Error("stepPath is required");
  }
  const resolvedRepoRoot = path.resolve(repoRoot);
  const resolvedStepPath = path.resolve(stepPath);
  const extension = path.extname(resolvedStepPath) || ".step";
  const normalizedCadPath = cadPath || cadPathForStepSource(resolvedRepoRoot, resolvedStepPath, extension);
  const validation = validateStepTopologyArtifact({
    repoRoot: resolvedRepoRoot,
    sourcePath: resolvedStepPath,
    cadPath: normalizedCadPath,
  });
  const stepArtifact = validation.stepArtifact || {};
  const artifact = catalogArtifactFromValidation(stepArtifact) || null;
  const normalizedPythonSourcePath = pythonSourcePath
    ? repoRelativePath(
        resolvedRepoRoot,
        path.isAbsolute(pythonSourcePath)
          ? path.resolve(pythonSourcePath)
          : path.resolve(resolvedRepoRoot, pythonSourcePath),
      )
    : "";
  const hasPythonSource = Boolean(normalizedPythonSourcePath) ||
    String(stepArtifact.sourceKind || artifact?.sourceKind || "").trim().toLowerCase() === "python";
  const sourceKind = hasPythonSource ? "python" : "step";
  const file = repoRelativePath(resolvedRepoRoot, resolvedStepPath);
  const sourcePath = String(stepArtifact.sourcePath || artifact?.sourcePath || normalizedPythonSourcePath || "").trim();
  const base = {
    ok: true,
    file,
    stepPath: file,
    sourceKind,
    artifact,
    ...(sourcePath ? { sourcePath } : {}),
  };

  if (!fileStats(resolvedStepPath)) {
    return {
      ...base,
      ok: false,
      step: {
        ok: false,
        status: "missing",
        missing: true,
        stale: false,
        message: "STEP file is missing.",
      },
    };
  }

  return {
    ...base,
    step: {
      ok: true,
      status: "current",
      missing: false,
      stale: false,
      currentHash: sha256File(resolvedStepPath),
    },
  };
}

function stepKindFromTopology(topology) {
  const index = topology?.index && typeof topology.index === "object" ? topology.index : topology;
  if (topology?.entryKind === "assembly" || index?.entryKind === "assembly") {
    return "assembly";
  }
  return index?.assembly?.root && typeof index.assembly.root === "object"
    ? "assembly"
    : "part";
}

function sourceFormatFromExtension(extension) {
  const normalized = extension.toLowerCase().replace(/^\./, "");
  return normalized === "stp" ? "stp" : normalized;
}

function sourceFormatForPath(sourcePath, extension = path.extname(sourcePath)) {
  return sourceFormatFromExtension(extension);
}

function isPerUrdfViewerDirectoryName(name) {
  const normalized = String(name || "").toLowerCase();
  return normalized.startsWith(".") && normalized.endsWith(".urdf");
}

function isHiddenDirectoryName(name) {
  return String(name || "").startsWith(".");
}

function isPathInsidePerUrdfViewerDirectory(filePath) {
  return String(filePath || "")
    .split(path.sep)
    .some((part) => isPerUrdfViewerDirectoryName(part));
}

function fileRefForSource(rootPath, sourcePath) {
  return scanRelativePath(rootPath, sourcePath);
}

function cadPathForStepSource(repoRoot, sourcePath, extension) {
  const relativePath = repoRelativePath(repoRoot, sourcePath);
  return relativePath.slice(0, -extension.length);
}

function sourcePathForInlineStepGlbArtifact(glbPath) {
  const name = path.basename(glbPath);
  if (!isInlineStepGlbArtifactPath(glbPath)) {
    return null;
  }
  return path.join(path.dirname(glbPath), name.slice(1, -".glb".length));
}

function sourcePathForInlineStepParameter(parameterPath) {
  const name = path.basename(parameterPath);
  if (!isInlineStepParameterPath(parameterPath)) {
    return null;
  }
  return path.join(path.dirname(parameterPath), name.slice(1, -".js".length));
}

function catalogArtifactFromValidation(stepArtifact) {
  if (!stepArtifact || typeof stepArtifact !== "object") {
    return undefined;
  }
  if (stepArtifact.ok) {
    return undefined;
  }
  const rawError = stepArtifact.error && typeof stepArtifact.error === "object"
    ? stepArtifact.error
    : {};
  const error = String(rawError.code || stepArtifact.error || "step_artifact_error").trim();
  const message = String(rawError.message || "").trim();
  const sourceKind = String(rawError.sourceKind || "").trim();
  const artifactHash = String(rawError.artifactHash || "").trim();
  const currentHash = String(rawError.currentHash || "").trim();
  const sourceHash = String(rawError.sourceHash || "").trim();
  const stepPath = String(rawError.stepPath || "").trim();
  const glbPath = String(rawError.glbPath || "").trim();
  const cadPath = String(rawError.cadPath || "").trim();
  return {
    ok: false,
    error,
    ...(rawError.stale ? { stale: true } : {}),
    ...(stepPath ? { stepPath } : {}),
    ...(glbPath ? { glbPath } : {}),
    ...(cadPath ? { cadPath } : {}),
    ...(sourceKind ? { sourceKind } : {}),
    ...(sourceHash ? { sourceHash } : {}),
    ...(artifactHash ? { artifactHash } : {}),
    ...(currentHash ? { currentHash } : {}),
    ...(message ? { message } : {}),
  };
}

function readStepCatalogMetadata({ repoRoot, glbPath, sourcePath = "" } = {}) {
  if (!fileStats(glbPath)) {
    return {};
  }
  let container = null;
  try {
    container = readGlbTopologyContainer(glbPath);
    const extension = container.gltf?.extensions?.[STEP_TOPOLOGY_EXTENSION];
    if (!extension || typeof extension !== "object" || Array.isArray(extension)) {
      return {};
    }
    if (!isCurrentStepTopologySchemaVersion(extension.schemaVersion)) {
      return {};
    }
    const manifest = parseJsonBufferView(
      container.fd,
      container.gltf,
      container.binOffset,
      container.binLength,
      extension.indexView,
      extension.encoding
    );
    const sourceKind = String(manifest?.sourceKind || "step").trim().toLowerCase() === "python"
      ? "python"
      : "step";
    const manifestIdentityRoot = manifestIdentityRootForStep(repoRoot, sourcePathForInlineStepGlbArtifact(glbPath) || sourcePath, manifest?.stepPath);
    const sourceIdentity = sourceKind === "python"
      ? generatorSourcePathFromManifest(repoRoot, manifest?.sourcePath, {
          identityRoot: manifestIdentityRoot,
          baseDir: path.dirname(glbPath),
        })
      : sourcePathFromManifest(repoRoot, manifest?.sourcePath, {
          identityRoot: manifestIdentityRoot,
          baseDir: path.dirname(glbPath),
        });
    return {
      topology: {
        index: manifest,
        entryKind: String(extension.entryKind || manifest?.entryKind || "").trim().toLowerCase(),
        hasSelector: Boolean(extension.selectorView),
        hasDisplayEdges: Boolean(extension.edgeView),
      },
      sourceKind,
      sourcePath: sourceIdentity.sourcePath,
      sourceHash: String(manifest?.sourceHash || ""),
      stepHash: String(manifest?.stepHash || ""),
    };
  } catch {
    return {};
  } finally {
    if (container?.fd !== null && container?.fd !== undefined) {
      try {
        fs.closeSync(container.fd);
      } catch {
        // Ignore close failures during lightweight catalog metadata reads.
      }
    }
  }
}

function pythonStepSourceFromStepMetadata(repoRoot, stepPath) {
  const metadata = readGeneratedFileMetadata(stepPath, "step");
  const metadataSourcePath = String(metadata.sourcePath || "").trim();
  if (!metadataSourcePath) {
    return null;
  }
  // Refuse untrusted metadata that names an absolute path, drive/UNC
  // anchor, null byte, or escapes ``repoRoot`` under realpath. This
  // mirrors ``resolveStepEntryPath``/``generatorFromStepMetadata`` --
  // the STEP file is attacker-controlled data, so its
  // ``cadgen:sourcePath`` must be treated the same way at every
  // consumption seam.
  const trusted = resolveTrustedMetadataPath(stepPath, metadataSourcePath, repoRoot);
  if (!trusted) {
    return null;
  }
  if (path.extname(trusted).toLowerCase() !== ".py") {
    return null;
  }
  return {
    sourcePath: repoRelativePath(repoRoot, trusted),
    sourceHash: String(metadata.sourceHash || ""),
  };
}

function createStepEntry({
  repoRoot,
  rootPath,
  sourcePath,
  extension,
  includeArtifactStatus = true,
  packageEntryMap = null,
  ambiguousLogicalSteps = null,
}) {
  const cadPath = cadPathForStepSource(repoRoot, sourcePath, extension);
  const resolvedSourceAbs = path.resolve(sourcePath);
  const resolvedSourceKey = filesystemPathIdentity(resolvedSourceAbs);
  // ``ambiguousLogicalSteps`` names logical STEP paths that two or
  // more descriptors both claim. Fail closed for these -- no
  // fallback inference at all -- so the client cannot silently
  // adopt an arbitrary winner.
  if (ambiguousLogicalSteps && ambiguousLogicalSteps.has(resolvedSourceKey)) {
    return {
      file: fileRefForSource(rootPath, sourcePath),
      kind: "part",
      url: assetUrlForPath(repoRoot, sourcePath),
      hash: "",
      bytes: 0,
      sourceKind: "step",
      artifact: {
        ok: false,
        error: "ambiguous_package_binding",
        stepPath: repoRelativePath(repoRoot, sourcePath),
        message: (
          `Multiple cadgen packages claim ${repoRelativePath(repoRoot, sourcePath)} as their logical STEP output; `
          + "refusing to adopt any of them. Remove one of the conflicting sources/*/gen_step() entries or "
          + "distinguish their target STEP paths."
        ),
      },
    };
  }
  // Authoritative logical-STEP -> package binding from the scan-wide
  // descriptor index. Non-same-stem builds
  // (``sources/assembly.py -> generated/robot.step``) require this;
  // filename inference cannot recover the generator location.
  const authoritativeBinding = packageEntryMap
    ? packageEntryMap.get(resolvedSourceKey)
    : null;
  const authoritativeEntry = authoritativeBinding?.entryPath || null;
  // The cadgen entry keys the render package; ``render_package_dir``
  // is keyed by the entry FILENAME, so a python-generated STEP whose
  // generator lives at ``sources/assembly.py`` has its package at
  // ``sources/__cadgen__/models/assembly.py`` -- NOT at
  // ``generated/__cadgen__/models/robot.py``. Filename inference
  // (same directory, same stem) only handles the trivial case; for
  // the explicit non-same-stem case we read the STEP file's embedded
  // metadata (``cadgen:sourcePath``, cadgen's authoritative record of
  // which generator produced it -- see
  // ``packages/cadgen/src/cadgen/_internal/step_metadata.py``).
  const inferredEntry = authoritativeEntry
    || resolveStepEntryPath(sourcePath, extension, { trustedRoot: repoRoot });
  const inferredEntryRelative = path.relative(rootPath, inferredEntry);
  // A narrowed viewer root cannot serve a sibling generator/package.
  // Treat metadata that points outside the active root as descriptive
  // only and validate this entry as an imported STEP instead.
  const entryPath = relativePathStaysInsideRoot(inferredEntryRelative)
    ? inferredEntry
    : sourcePath;
  const validation = includeArtifactStatus
    ? validateStepTopologyArtifact({
        repoRoot,
        sourcePath,
        cadPath,
        entryPath,
      })
    : null;
  const glbPath = validation?.glbPath || inlineStepGlbArtifactPathForSource(sourcePath);
  // The canonical viewer asset for a STEP entry is the cadgen render
  // package directory, not a single inline GLB (see
  // viewer/src/client/components/workbench/hooks/useCadAssets.js and
  // viewer/server_py/scanner.py::create_step_entry). For a Python-
  // generated STEP the entry is the ``.py`` generator; for imported
  // STEP the entry is the STEP file itself. Prefer the validated
  // package directory when one exists; fall back to the legacy inline
  // GLB path only when no cadgen package has been built yet.
  // The authoritative binding, when present, already knows this STEP
  // is python-generated and where the render package lives, even when
  // ``includeArtifactStatus`` is false (fast catalog builds skip the
  // freshness verification). Use it to seed ``sourceKind`` / package
  // asset so a descriptor-only mapping still surfaces as python with
  // a package-scoped URL.
  const packageDir = validation?.packageDir || authoritativeBinding?.packageDir || null;
  const packageAssetPath = validation?.stepArtifact?.ok === true && packageDir && fileStats(path.join(packageDir, CADGEN_PACKAGE_DESCRIPTOR))
    ? packageDir
    : "";
  const catalogMetadata = includeArtifactStatus
    ? {}
    : readStepCatalogMetadata({ repoRoot, glbPath, sourcePath });
  const topology = validation?.topology || catalogMetadata.topology || null;
  const stepArtifact = validation?.stepArtifact || {};
  const glbAsset = packageAssetPath
    ? assetForPath(repoRoot, packageAssetPath)
    : assetForPath(repoRoot, glbPath);
  const stepModuleAsset = assetForPath(repoRoot, stepParameterPathForStepSource(sourcePath));
  const artifact = includeArtifactStatus ? catalogArtifactFromValidation(stepArtifact) : undefined;
  const bindingSourceKind = authoritativeBinding?.sourceKind
    ? String(authoritativeBinding.sourceKind).trim().toLowerCase()
    : "";
  const artifactSourceKind = String(
    stepArtifact.sourceKind ||
    stepArtifact.error?.sourceKind ||
    catalogMetadata.sourceKind ||
    artifact?.sourceKind ||
    bindingSourceKind ||
    "",
  ).trim().toLowerCase();
  const metadataPythonSource = (!includeArtifactStatus || artifact) && artifactSourceKind !== "python"
    ? pythonStepSourceFromStepMetadata(repoRoot, sourcePath)
    : null;
  const sourceKind = artifactSourceKind === "python" || metadataPythonSource ? "python" : "step";
  const artifactSourcePath = String(
    stepArtifact.sourcePath ||
    stepArtifact.error?.sourcePath ||
    catalogMetadata.sourcePath ||
    ""
  ).trim();
  // Prefer the authoritative binding's absolute entryPath (converted
  // to repo-relative POSIX so ``absolutizeCatalogEntry`` can resolve
  // it against ``scanRepoRoot`` without treating cadgen's step-dir-
  // relative ``descriptor.sourcePath`` string as repo-relative -- which
  // otherwise walks the source path outside the repo).
  const authoritativePythonSource = authoritativeEntry
    ? repoRelativePath(repoRoot, authoritativeEntry)
    : "";
  const pythonSourcePath = authoritativePythonSource
    || artifactSourcePath
    || metadataPythonSource?.sourcePath
    || "";
  return {
    file: fileRefForSource(rootPath, sourcePath),
    kind: stepKindFromTopology(topology),
    url: glbAsset?.url || assetUrlForPath(repoRoot, packageAssetPath || glbPath),
    hash: glbAsset?.hash || "",
    bytes: glbAsset?.bytes || 0,
    sourceKind,
    ...(sourceKind === "python" && pythonSourcePath ? {
      source: {
        file: pythonSourcePath,
        sourcePath: pythonSourcePath,
        ...((stepArtifact.sourceHash || catalogMetadata.sourceHash || metadataPythonSource?.sourceHash)
          ? { sourceHash: stepArtifact.sourceHash || catalogMetadata.sourceHash || metadataPythonSource.sourceHash }
          : {}),
      },
    } : {}),
    ...(stepModuleAsset ? { moduleUrl: stepModuleAsset.url } : {}),
    ...(artifact ? { artifact } : {}),
  };
}

function linkedUrdfPathForSrdf(sourcePath, repoRoot) {
  let xmlText = "";
  try {
    xmlText = fs.readFileSync(sourcePath, "utf-8");
  } catch {
    return null;
  }
  const match = SRDF_URDF_METADATA_PATTERN.exec(xmlText);
  const rawRef = String(match?.[1] || "").trim();
  if (!rawRef || rawRef.includes("\\") || rawRef.startsWith("/")) {
    return null;
  }
  const resolved = path.resolve(path.dirname(sourcePath), rawRef);
  const relativeToRepo = path.relative(path.resolve(repoRoot), resolved);
  if (!relativePathStaysInsideRoot(relativeToRepo) || path.extname(resolved).toLowerCase() !== ".urdf") {
    return null;
  }
  return fileStats(resolved) ? resolved : null;
}

function createSingleAssetEntry({ repoRoot, rootPath, sourcePath, extension }) {
  const kind = sourceFormatForPath(sourcePath, extension);
  const asset = assetForPath(repoRoot, sourcePath);
  const file = fileRefForSource(rootPath, sourcePath);
  const generatedSource = generatedSourceStatusForFile({ repoRoot, sourcePath, kind });
  const entry = {
    file,
    kind,
    url: asset?.url || assetUrlForPath(repoRoot, sourcePath),
    hash: asset?.hash || "",
    bytes: asset?.bytes || 0,
    ...(generatedSource?.sourceKind === "python" ? { sourceKind: "python" } : {}),
    ...(generatedSource?.source ? { source: generatedSource.source } : {}),
    ...(generatedSource?.sourceStatus ? { sourceStatus: generatedSource.sourceStatus } : {}),
  };
  if (kind === "srdf") {
    const linkedUrdfPath = linkedUrdfPathForSrdf(sourcePath, repoRoot);
    if (linkedUrdfPath) {
      const urdfAsset = assetForPath(repoRoot, linkedUrdfPath);
      if (urdfAsset) {
        entry.relations = {
          urdf: {
            file: fileRefForSource(rootPath, linkedUrdfPath),
            ...urdfAsset,
          },
        };
      }
    }
  }
  return entry;
}

function shouldSkipDirectory(name) {
  return VIEWER_SKIPPED_DIRECTORIES.has(name) || isHiddenDirectoryName(name) || isPerStepViewerDirectoryName(name) || isPerUrdfViewerDirectoryName(name);
}

function scanPathIncluded(includePath, rootPath, entryPath, isDirectory) {
  if (typeof includePath !== "function") {
    return true;
  }
  return includePath({
    filePath: entryPath,
    relativePath: scanRelativePath(rootPath, entryPath),
    isDirectory,
  }) !== false;
}

function collectCadSourceFiles(rootPath, { scanRootPath = rootPath, includePath = null, trustedRoot = null } = {}, result = []) {
  let entries = [];
  try {
    entries = fs.readdirSync(rootPath, { withFileTypes: true });
  } catch {
    return result;
  }

  for (const entry of entries) {
    const entryPath = path.join(rootPath, entry.name);
    if (entry.isDirectory()) {
      if (!shouldSkipDirectory(entry.name) && scanPathIncluded(includePath, scanRootPath, entryPath, true)) {
        collectCadSourceFiles(entryPath, { scanRootPath, includePath, trustedRoot }, result);
      }
      continue;
    }
    if (!entry.isFile()) {
      continue;
    }
    if (!scanPathIncluded(includePath, scanRootPath, entryPath, false)) {
      continue;
    }
    const extension = path.extname(entry.name).toLowerCase();
    if (isInlineStepGlbArtifactPath(entryPath)) {
      const sourcePath = sourcePathForInlineStepGlbArtifact(entryPath);
      if (sourcePath && !fileStats(sourcePath)) {
        result.push(entryPath);
      }
      continue;
    }
    if (SOURCE_EXTENSIONS.has(extension) && !isInlineStepGlbArtifactPath(entryPath)) {
      result.push(entryPath);
      continue;
    }
    // Python STEP generator without an on-disk STEP file. cadgen writes
    // the render package under ``<py.parent>/__cadgen__/models/<py.name>/``
    // and there is no monolithic inline GLB, so a python-only entry
    // must be discovered from the ``.py`` source directly. When the
    // cadgen descriptor is present it records the authoritative
    // ``stepPath`` (which can be non-same-stem, e.g.
    // ``sources/assembly.py -> generated/robot.step``); use that
    // instead of inventing a filename. When no descriptor exists we
    // fall back to same-stem sibling, matching cadgen's own default
    // (``_generator_sibling`` in ``packages/cadgen/src/cadgen/catalog.py``).
    if (extension === ".py") {
      try {
        const contents = fs.readFileSync(entryPath, "utf-8");
        if (/\bgen_step\s*\(/.test(contents)) {
          const logicalStepPath = _logicalStepPathForGenerator(entryPath, { trustedRoot });
          const logicalRelative = logicalStepPath
            ? path.relative(scanRootPath, logicalStepPath)
            : "";
          if (
            logicalStepPath
            && relativePathStaysInsideRoot(logicalRelative)
            && !fileStats(logicalStepPath)
          ) {
            result.push(logicalStepPath);
          }
        }
      } catch {
        // Unreadable Python source is ignored during discovery.
      }
    }
  }
  return result;
}

function compareEntries(a, b) {
  return String(a.file || "").localeCompare(String(b.file || ""), undefined, {
    numeric: true,
    sensitivity: "base",
  });
}

function pathHasSkippedDirectory(rootPath, filePath) {
  const relativePath = scanRelativePath(rootPath, filePath);
  if (!relativePathStaysInsideRoot(relativePath)) {
    return true;
  }
  const parts = relativePath.split("/").filter(Boolean);
  return parts.slice(0, -1).some((part) => shouldSkipDirectory(part));
}

function logicalStepSourcePathForInlineArtifactPath(filePath) {
  if (isInlineStepGlbArtifactPath(filePath)) {
    return sourcePathForInlineStepGlbArtifact(filePath);
  }
  if (isInlineStepParameterPath(filePath)) {
    return sourcePathForInlineStepParameter(filePath);
  }
  return null;
}

function logicalStepSourceExistsForSidecar(sourcePath) {
  return Boolean(
    sourcePath &&
    (
      fileStats(sourcePath) ||
      fileStats(inlineStepGlbArtifactPathForSource(sourcePath)) ||
      fileStats(stepParameterPathForStepSource(sourcePath))
    )
  );
}

export function catalogFileRefForPath({ repoRoot, rootDir = DEFAULT_VIEWER_ROOT_DIR, filePath } = {}) {
  if (!repoRoot) {
    throw new Error("repoRoot is required");
  }
  if (!filePath) {
    throw new Error("filePath is required");
  }
  const resolved = resolveViewerRoot(repoRoot, rootDir);
  const resolvedFilePath = path.resolve(filePath);
  if (!pathIsInside(resolvedFilePath, resolved.rootPath) || pathHasSkippedDirectory(resolved.rootPath, resolvedFilePath)) {
    return "";
  }
  const logicalStepSourcePath = logicalStepSourcePathForInlineArtifactPath(resolvedFilePath);
  if (logicalStepSourcePath) {
    return fileRefForSource(resolved.rootPath, logicalStepSourcePath);
  }
  if (isPathInsidePerStepViewerDirectory(resolvedFilePath) || isPathInsidePerUrdfViewerDirectory(resolvedFilePath)) {
    return "";
  }
  const extension = path.extname(resolvedFilePath).toLowerCase();
  return SOURCE_EXTENSIONS.has(extension)
    ? fileRefForSource(resolved.rootPath, resolvedFilePath)
    : "";
}

export function scanCadFile({
  repoRoot,
  rootDir = DEFAULT_VIEWER_ROOT_DIR,
  filePath,
  includeArtifactStatus = true,
  // Optional authoritative ambiguity context from the caller's last
  // full ``scanCadDirectory`` scan (e.g. cached by
  // ``localAssetBackend``). Must be a Set of resolved absolute STEP
  // paths OR ``null``. The targeted binding cannot re-derive full-
  // tree ambiguity from a single STEP file; without this hint a
  // request-shaped per-file refresh would downgrade an authoritative
  // ``ambiguous_package_binding`` back to an accepted mapping when the
  // STEP's own metadata names only ONE of the conflicting packages.
  // Never trust client input for this field; the caller MUST supply a
  // Set derived from a prior authoritative full scan or ``null``.
  authoritativeAmbiguousSteps = null,
} = {}) {
  if (!repoRoot) {
    throw new Error("repoRoot is required");
  }
  if (!filePath) {
    throw new Error("filePath is required");
  }
  const resolved = resolveViewerRoot(repoRoot, rootDir);
  const resolvedFilePath = path.resolve(filePath);
  if (!pathIsInside(resolvedFilePath, resolved.rootPath) || pathHasSkippedDirectory(resolved.rootPath, resolvedFilePath)) {
    return null;
  }

  const logicalStepSourcePath = logicalStepSourcePathForInlineArtifactPath(resolvedFilePath);
  if (logicalStepSourcePath) {
    if (!logicalStepSourceExistsForSidecar(logicalStepSourcePath)) {
      return null;
    }
    return createStepEntry({
      repoRoot,
      rootPath: resolved.rootPath,
      sourcePath: logicalStepSourcePath,
      extension: path.extname(logicalStepSourcePath).toLowerCase(),
      includeArtifactStatus,
    });
  }

  if (isPathInsidePerStepViewerDirectory(resolvedFilePath) || isPathInsidePerUrdfViewerDirectory(resolvedFilePath)) {
    return null;
  }

  const extension = path.extname(resolvedFilePath).toLowerCase();
  if (!SOURCE_EXTENSIONS.has(extension) || !fileStats(resolvedFilePath)) {
    if ((extension === ".step" || extension === ".stp") && fileStats(inlineStepGlbArtifactPathForSource(resolvedFilePath))) {
      return createStepEntry({
        repoRoot,
        rootPath: resolved.rootPath,
        sourcePath: resolvedFilePath,
        extension,
        includeArtifactStatus,
      });
    }
    // Descriptor-only non-same-stem mapping: the STEP file is not on
    // disk but a cadgen render-package descriptor elsewhere in the
    // tree names it as its ``stepPath``. Reconstruct the SAME entry
    // ``scanCadDirectory`` would produce for it -- routed through the
    // authoritative ``bindCadgenPackage`` binder (via
    // ``_buildPackageEntryMap``) so no stale/forged entry ever
    // survives without full binder validation.
    if (extension === ".step" || extension === ".stp") {
      const { map: pkgMap, ambiguous } = _buildPackageEntryMap(resolved.rootPath, repoRoot);
      const resolvedFileKey = filesystemPathIdentity(resolvedFilePath);
      if (pkgMap.has(resolvedFileKey) || (ambiguous && ambiguous.has(resolvedFileKey))) {
        return createStepEntry({
          repoRoot,
          rootPath: resolved.rootPath,
          sourcePath: resolvedFilePath,
          extension,
          includeArtifactStatus,
          packageEntryMap: pkgMap,
          ambiguousLogicalSteps: ambiguous,
        });
      }
    }
    return null;
  }
  if (extension === ".step" || extension === ".stp") {
    // Existing-STEP targeted binding: derive candidate package
    // locations from the STEP file itself (same-stem sibling,
    // imported-STEP-keyed, and the STEP's own ``cadgen:sourcePath``
    // metadata) and validate each through the trusted binder. This
    // preserves the documented O(single entry) incremental contract
    // -- ``scanCadFile`` for an ordinary STEP never recurses into
    // unrelated ``__cadgen__`` directories elsewhere in the tree.
    // Ambiguity / fail-closed binding is the SAME code path as
    // ``scanCadDirectory``; only the discovery is narrower.
    const { map: pkgMap, ambiguous } = _targetedPackageBindingForStep(resolvedFilePath, repoRoot);
    // The targeted binding only sees candidates reachable from THIS
    // STEP file (same-stem sibling, imported self-package, and the
    // STEP's own ``cadgen:sourcePath`` metadata). A prior full
    // ``scanCadDirectory`` may have discovered a SECOND descriptor
    // elsewhere in the tree that also claims this logical STEP --
    // the authoritative ambiguity that a request-shaped targeted
    // refresh cannot re-derive on its own. Callers thread that
    // authoritative set in via ``authoritativeAmbiguousSteps`` so
    // targeted refresh may strengthen ``ok`` -> ``error`` but MUST
    // NOT downgrade an authoritative
    // ``ambiguous_package_binding`` back to an accepted binding.
    // (Only a subsequent full ``scanCadDirectory`` reconciles the
    // ambiguous set with the current on-disk descriptor state.)
    const strengthenedAmbiguous = _mergeAuthoritativeAmbiguity(
      ambiguous,
      authoritativeAmbiguousSteps,
      resolvedFilePath,
    );
    // If the authoritative set upgrades this STEP to ambiguous, the
    // targeted binding's accepted mapping must not survive either --
    // otherwise ``createStepEntry`` would still pick up the sole
    // targeted binding through ``packageEntryMap`` when a caller
    // (e.g. later refactor) checks the map before the ambiguity set.
    // Drop it here so the ambiguity branch is the only accessible
    // outcome.
    const resolvedFileKey = filesystemPathIdentity(resolvedFilePath);
    const strengthenedMap = strengthenedAmbiguous !== ambiguous && strengthenedAmbiguous.has(resolvedFileKey)
      ? new Map([...pkgMap.entries()].filter(([key]) => key !== resolvedFileKey))
      : pkgMap;
    return createStepEntry({
      repoRoot,
      rootPath: resolved.rootPath,
      sourcePath: resolvedFilePath,
      extension,
      includeArtifactStatus,
      packageEntryMap: strengthenedMap,
      ambiguousLogicalSteps: strengthenedAmbiguous,
    });
  }
  return createSingleAssetEntry({
    repoRoot,
    rootPath: resolved.rootPath,
    sourcePath: resolvedFilePath,
    extension,
  });
}

export function scanCadDirectory({
  repoRoot,
  rootDir = DEFAULT_VIEWER_ROOT_DIR,
  includePath = null,
  includeArtifactStatus = true,
} = {}) {
  if (!repoRoot) {
    throw new Error("repoRoot is required");
  }
  const resolved = resolveViewerRoot(repoRoot, rootDir);
  // Build the authoritative "logical STEP path -> generator entry"
  // map from cadgen package descriptors under ``resolved.rootPath``.
  // For every ``.py`` generator with a ``__cadgen__/models/<name>/``
  // package, the descriptor's ``stepPath`` field (relative to the
  // generator's parent dir) points at the STEP location cadgen
  // actually writes. Same-directory same-stem maps land at the STEP's
  // natural location and are indistinguishable from the fallback;
  // non-same-stem maps (``sources/assembly.py -> generated/robot.step``)
  // point at a directory the scanner would otherwise never associate
  // with the generator. Ambiguity (two generators pointing at the
  // same logical STEP) fails closed.
  const { map: packageEntryMap, ambiguous: ambiguousLogicalSteps } =
    _buildPackageEntryMap(resolved.rootPath, repoRoot);
  // Deduplicate collected paths on the LOGICAL source they represent
  // BEFORE materializing catalog entries. Two python packages that
  // both descriptor-declare the same missing logical STEP would each
  // cause ``collectCadSourceFiles`` to emit that STEP path; without
  // dedupe the catalog would contain two rows with identical ``file``
  // identity, both marked ``ambiguous_package_binding``. Catalog file
  // identity MUST be unique, and an ambiguous mapping is represented
  // by exactly ONE deterministic fail-closed diagnostic entry.
  const collected = collectCadSourceFiles(resolved.rootPath, {
    scanRootPath: resolved.rootPath,
    includePath,
    trustedRoot: repoRoot,
  });
  const seenLogicalSource = new Set();
  const uniqueSources = [];
  for (const sourcePath of collected) {
    const logicalSourcePath = isInlineStepGlbArtifactPath(sourcePath)
      ? sourcePathForInlineStepGlbArtifact(sourcePath)
      : sourcePath;
    const dedupeKey = filesystemPathIdentity(logicalSourcePath);
    if (seenLogicalSource.has(dedupeKey)) {
      continue;
    }
    seenLogicalSource.add(dedupeKey);
    uniqueSources.push(sourcePath);
  }
  const entries = uniqueSources
    .map((sourcePath) => {
      const logicalSourcePath = isInlineStepGlbArtifactPath(sourcePath)
        ? sourcePathForInlineStepGlbArtifact(sourcePath)
        : sourcePath;
      const extension = path.extname(logicalSourcePath).toLowerCase();
      if (extension === ".step" || extension === ".stp") {
        return createStepEntry({
          repoRoot,
          rootPath: resolved.rootPath,
          sourcePath: logicalSourcePath,
          extension,
          includeArtifactStatus,
          packageEntryMap,
          ambiguousLogicalSteps,
        });
      }
      return createSingleAssetEntry({
        repoRoot,
        rootPath: resolved.rootPath,
        sourcePath,
        extension,
      });
    })
    .sort(compareEntries);

  // Expose the authoritative "logical STEP -> ambiguous" set produced
  // by this full scan so callers (e.g. ``localAssetBackend``) can
  // preserve it across subsequent request-shaped per-file refreshes.
  // A targeted ``scanCadFile`` for one of these STEPs cannot re-derive
  // multi-descriptor ambiguity from a single file; without this hint,
  // an incremental refresh whose targeted binding sees only ONE of the
  // conflicting packages would silently downgrade the entry from
  // ``ambiguous_package_binding`` to an accepted mapping.  Emitted as
  // an array of resolved absolute POSIX-friendly paths (JSON-safe);
  // this field is dropped by ``normalizeCatalog`` downstream (which
  // only preserves ``schemaVersion`` + ``entries``), so callers who
  // want to use it must read it BEFORE catalog normalization.
  const authoritativeAmbiguousStepPaths = [...ambiguousLogicalSteps]
    .map((p) => path.resolve(p));
  return {
    schemaVersion: CAD_CATALOG_SCHEMA_VERSION,
    entries,
    authoritativeAmbiguousStepPaths,
  };
}

export function sortCatalogEntries(entries) {
  return [...(Array.isArray(entries) ? entries : [])].sort(compareEntries);
}

export function isServedCadAsset(filePath) {
  const extension = path.extname(filePath).toLowerCase();
  if (isInlineStepGlbArtifactPath(filePath)) {
    return true;
  }
  if (isInlineStepParameterPath(filePath)) {
    return true;
  }
  if (isCadgenPackageDescriptorPath(filePath)) {
    return true;
  }
  if (isPathInsidePerStepViewerDirectory(filePath)) {
    return false;
  }
  if (isPathInsidePerUrdfViewerDirectory(filePath)) {
    return false;
  }
  if (SOURCE_EXTENSIONS.has(extension)) {
    return true;
  }
  return false;
}

function isCatalogRelevantPythonSource(filePath) {
  const normalized = path.normalize(String(filePath || ""));
  if (path.extname(normalized).toLowerCase() !== ".py") {
    return false;
  }
  return !normalized.split(/[\\/]+/u).some((part) => VIEWER_SKIPPED_DIRECTORIES.has(part));
}

// Recognize cadgen package descriptor paths so a watcher event on the
// descriptor itself is routed to a FULL catalog refresh -- the only
// way to reconcile authoritative "logical STEP -> ambiguous" state
// after a package is added or removed. Descriptors live at
// ``<any>/__cadgen__/models/<pkg>/assembly.json``. Anywhere else the
// filename would just be an ordinary JSON file we do not care about.
export function isCadgenPackageDescriptorPath(filePath) {
  const normalized = path.normalize(String(filePath || ""));
  if (path.basename(normalized) !== CADGEN_PACKAGE_DESCRIPTOR) {
    return false;
  }
  const packageDir = path.dirname(normalized);
  const modelsDir = path.dirname(packageDir);
  const cadgenDir = path.dirname(modelsDir);
  if (path.basename(modelsDir) !== CADGEN_MODELS_DIRNAME) return false;
  if (path.basename(cadgenDir) !== CADGEN_DIRNAME) return false;
  return true;
}

export function isCatalogRelevantPath(filePath) {
  return (
    isServedCadAsset(filePath)
    || isCatalogRelevantPythonSource(filePath)
    || isCadgenPackageDescriptorPath(filePath)
  );
}
