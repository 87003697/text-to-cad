// Exploded-view step document: model, normalization, and evaluator.
//
// An exploded view is an ordered list of explode *steps*, mirroring how
// SolidWorks / Onshape / Fusion model disassembly. Each step moves a set of
// occurrence-path targets rigidly by an exact amount:
//   - translate: along an axis by a distance (model units)
//   - rotate:    about an axis through an origin by an angle (degrees)
//   - radial:    outward from an axis, each target in its own resolved direction
//
// The compiler resolves a document + display records + bounds into a per-record
// plan of contributions; the evaluator writes a pure `record.explodedViewMatrix`
// at a global 0..1 scrub `progress`. This is the single hand-off contract the
// renderer, topology edges, snapshot export, and zoom-to-part all read.
//
// This module is UI-framework agnostic and takes THREE by dependency injection.

export const EPSILON = 1e-6;
export const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));

export const EXPLODED_VIEW_STEP_TYPES = Object.freeze(["translate", "rotate", "radial"]);
export const EXPLODED_VIEW_ORDERS = Object.freeze(["simultaneous", "sequential"]);
// Auto-explode generator modes. "auto" picks the principal axis from the
// component layout; x/y/z force a world axis; "radial" blooms about the axis.
export const EXPLODED_VIEW_AUTO_MODES = Object.freeze(["auto", "x", "y", "z", "radial"]);
export const EXPLODED_VIEW_DIRECTIONS = Object.freeze(["positive", "negative"]);
export const MAX_EXPLODED_VIEW_DEPTH = 8;
export const MIN_EXPLODE_GAP_SCALE = 0.25;
export const MAX_EXPLODE_GAP_SCALE = 4;

export function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

export function toNumber(value, fallback = 0) {
  const numericValue = Number(value);
  return Number.isFinite(numericValue) ? numericValue : fallback;
}

function isFiniteVectorArray(value) {
  return (Array.isArray(value) || ArrayBuffer.isView(value)) && value.length >= 3;
}

export function toVectorArray(value, fallback = [0, 0, 0]) {
  if (value?.isVector3) {
    return [value.x, value.y, value.z];
  }
  if (isFiniteVectorArray(value)) {
    return [toNumber(value[0]), toNumber(value[1]), toNumber(value[2])];
  }
  return [...fallback];
}

// -- occurrence path helpers ------------------------------------------------
// Occurrence ids look like "o1.2.3": the first segment is o<number>, the rest
// are numeric. A step target is an occurrence-path prefix; a record belongs to
// a target when the target's segments are a prefix of the record's segments,
// so naming a sub-assembly moves its whole subtree rigidly.

export function occurrenceSegments(partId) {
  const text = String(partId || "").trim();
  const match = text.match(/^o\d+(?:\.\d+)*/i);
  return match ? match[0].split(".").filter(Boolean) : [];
}

export function targetSegments(target) {
  const text = String(target || "").trim();
  const segments = occurrenceSegments(text);
  return segments.length ? segments : (text ? [text] : []);
}

export function targetMatchesRecord(targetSegs, recordPartId) {
  if (!Array.isArray(targetSegs) || !targetSegs.length) {
    return false;
  }
  const recordSegs = occurrenceSegments(recordPartId);
  if (recordSegs.length) {
    if (recordSegs.length < targetSegs.length) {
      return false;
    }
    return targetSegs.every((segment, index) => recordSegs[index] === segment);
  }
  // Non-occurrence part ids (e.g. "part:3", "__model__"): exact match only.
  return targetSegs.length === 1 && targetSegs[0] === String(recordPartId || "").trim();
}

// -- bounds helpers ---------------------------------------------------------

export function boundsCenterArray(bounds, fallback = [0, 0, 0]) {
  const min = isFiniteVectorArray(bounds?.min) ? bounds.min : null;
  const max = isFiniteVectorArray(bounds?.max) ? bounds.max : null;
  if (!min || !max) {
    return [...fallback];
  }
  return [
    (toNumber(min[0]) + toNumber(max[0])) / 2,
    (toNumber(min[1]) + toNumber(max[1])) / 2,
    (toNumber(min[2]) + toNumber(max[2])) / 2
  ];
}

export function boundsSize(bounds, axis) {
  const min = isFiniteVectorArray(bounds?.min) ? bounds.min : null;
  const max = isFiniteVectorArray(bounds?.max) ? bounds.max : null;
  if (!min || !max) {
    return 0;
  }
  return Math.max(toNumber(max[axis]) - toNumber(min[axis]), 0);
}

export function boundsRadius(bounds) {
  const x = boundsSize(bounds, 0);
  const y = boundsSize(bounds, 1);
  const z = boundsSize(bounds, 2);
  return Math.sqrt(x * x + y * y + z * z) / 2;
}

export function boundsMaxSize(bounds) {
  return Math.max(boundsSize(bounds, 0), boundsSize(bounds, 1), boundsSize(bounds, 2));
}

export function mergeBounds(boundsList) {
  const min = [Infinity, Infinity, Infinity];
  const max = [-Infinity, -Infinity, -Infinity];
  let count = 0;
  for (const bounds of Array.isArray(boundsList) ? boundsList : []) {
    if (!isFiniteVectorArray(bounds?.min) || !isFiniteVectorArray(bounds?.max)) {
      continue;
    }
    count += 1;
    for (let axis = 0; axis < 3; axis += 1) {
      min[axis] = Math.min(min[axis], toNumber(bounds.min[axis]));
      max[axis] = Math.max(max[axis], toNumber(bounds.max[axis]));
    }
  }
  return count > 0 && min.every(Number.isFinite) && max.every(Number.isFinite)
    ? { min, max }
    : null;
}

export function recordBounds(record) {
  const bounds = record?.partBounds;
  return isFiniteVectorArray(bounds?.min) && isFiniteVectorArray(bounds?.max) ? bounds : null;
}

export function recordCanExplode(record) {
  const partId = String(record?.partId || "").trim();
  return Boolean(record?.mesh && partId && partId !== "__model__");
}

// -- normalization ----------------------------------------------------------

function normalizeAxisArray(value, fallback = [0, 0, 1]) {
  const array = toVectorArray(value, fallback);
  const length = Math.hypot(array[0], array[1], array[2]);
  if (length <= EPSILON) {
    return [...fallback];
  }
  return [array[0] / length, array[1] / length, array[2] / length];
}

function normalizeStringArray(value) {
  const source = Array.isArray(value) ? value : (value == null ? [] : [value]);
  const seen = new Set();
  const out = [];
  for (const entry of source) {
    const text = String(entry ?? "").trim();
    if (text && !seen.has(text)) {
      seen.add(text);
      out.push(text);
    }
  }
  return out;
}

// Normalize a single step into a canonical, JSON-stable shape (fixed key order,
// only JSON-safe primitives) so displaySettingsEqual's JSON.stringify holds.
export function normalizeExplodeStep(value, index = 0) {
  if (!value || typeof value !== "object") {
    return null;
  }
  const rawType = String(value.type || "translate").trim().toLowerCase();
  const type = EXPLODED_VIEW_STEP_TYPES.includes(rawType) ? rawType : "translate";
  const targets = normalizeStringArray(value.targets ?? value.target);
  if (!targets.length) {
    return null;
  }
  const id = String(value.id || "").trim() || `s${index + 1}`;
  if (type === "rotate") {
    return {
      id,
      type,
      targets,
      axis: normalizeAxisArray(value.axis, [0, 0, 1]),
      origin: toVectorArray(value.origin, [0, 0, 0]),
      angleDeg: toNumber(value.angleDeg ?? value.angle, 0)
    };
  }
  if (type === "radial") {
    return {
      id,
      type,
      targets,
      axis: normalizeAxisArray(value.axis, [0, 0, 1]),
      center: toVectorArray(value.center, [0, 0, 0]),
      distance: toNumber(value.distance, 0)
    };
  }
  return {
    id,
    type,
    targets,
    axis: normalizeAxisArray(value.axis, [0, 0, 1]),
    distance: toNumber(value.distance, 0)
  };
}

export function normalizeExplodeAutoHints(value) {
  const source = value && typeof value === "object" ? value : {};
  const rawMode = String(source.mode ?? source.axis ?? "auto").trim().toLowerCase();
  const mode = EXPLODED_VIEW_AUTO_MODES.includes(rawMode)
    ? rawMode
    : (rawMode.replace(/^[-+]/, "") === "radial" ? "radial" : "auto");
  const rawDirection = String(source.direction ?? "").trim().toLowerCase();
  const direction = rawDirection.startsWith("-") || ["negative", "reverse", "down", "backward"].includes(rawDirection)
    ? "negative"
    : "positive";
  const depthRaw = Math.round(toNumber(source.depth ?? source.levels, 1));
  const depth = clamp(Number.isFinite(depthRaw) ? depthRaw : 1, 1, MAX_EXPLODED_VIEW_DEPTH);
  const gapScale = clamp(
    toNumber(source.gapScale ?? source.spacing ?? source.distanceScale, 1),
    MIN_EXPLODE_GAP_SCALE,
    MAX_EXPLODE_GAP_SCALE
  );
  return {
    mode,
    direction,
    depth,
    gapScale,
    keepBaseGrounded: source.keepBaseGrounded === undefined ? true : Boolean(source.keepBaseGrounded)
  };
}

// The full normalized exploded-view document embedded in display settings.
export function normalizeExplodedViewDocument(value, overrides = {}) {
  const source = value && typeof value === "object" ? value : {};
  const merged = overrides && typeof overrides === "object" ? { ...source, ...overrides } : source;
  const rawOrder = String(merged.order || "").trim().toLowerCase();
  const order = EXPLODED_VIEW_ORDERS.includes(rawOrder) ? rawOrder : "simultaneous";
  const steps = (Array.isArray(merged.steps) ? merged.steps : [])
    .map((step, index) => normalizeExplodeStep(step, index))
    .filter(Boolean);
  return {
    enabled: Boolean(merged.enabled),
    amount: clamp(toNumber(merged.amount, 1), 0, 1),
    order,
    trails: merged.trails === undefined ? false : Boolean(merged.trails),
    auto: normalizeExplodeAutoHints(merged.auto),
    steps
  };
}

// -- step-progress scheduling ----------------------------------------------

// Map a global 0..1 scrub position to a single step's local progress.
// "simultaneous": all steps track the global amount. "sequential": step k of n
// animates over the [k/n, (k+1)/n] slice, so a scrub walks the disassembly.
export function explodedViewStepProgress(order, index, count, progress) {
  const global = clamp(toNumber(progress), 0, 1);
  if (order !== "sequential" || count <= 1) {
    return global;
  }
  const span = 1 / count;
  return clamp((global - index * span) / span, 0, 1);
}

// -- compilation ------------------------------------------------------------

function fanDirection(axisArray, index) {
  // Build an in-plane direction perpendicular to axis for coaxial targets that
  // have no intrinsic radial direction; fan by the golden angle.
  const axis = normalizeAxisArray(axisArray, [0, 0, 1]);
  const helper = Math.abs(axis[2]) < 0.9 ? [0, 0, 1] : [1, 0, 0];
  // u = normalize(helper x axis); v = axis x u  -> orthonormal in-plane basis.
  const ux = helper[1] * axis[2] - helper[2] * axis[1];
  const uy = helper[2] * axis[0] - helper[0] * axis[2];
  const uz = helper[0] * axis[1] - helper[1] * axis[0];
  const uLen = Math.hypot(ux, uy, uz) || 1;
  const u = [ux / uLen, uy / uLen, uz / uLen];
  const v = [
    axis[1] * u[2] - axis[2] * u[1],
    axis[2] * u[0] - axis[0] * u[2],
    axis[0] * u[1] - axis[1] * u[0]
  ];
  const angle = index * GOLDEN_ANGLE;
  const c = Math.cos(angle);
  const s = Math.sin(angle);
  return [u[0] * c + v[0] * s, u[1] * c + v[1] * s, u[2] * c + v[2] * s];
}

function radialDirection(centerArray, axisArray, groupCenterArray, index) {
  const axis = normalizeAxisArray(axisArray, [0, 0, 1]);
  const rel = [
    groupCenterArray[0] - centerArray[0],
    groupCenterArray[1] - centerArray[1],
    groupCenterArray[2] - centerArray[2]
  ];
  const dot = rel[0] * axis[0] + rel[1] * axis[1] + rel[2] * axis[2];
  const perp = [rel[0] - axis[0] * dot, rel[1] - axis[1] * dot, rel[2] - axis[2] * dot];
  const length = Math.hypot(perp[0], perp[1], perp[2]);
  if (length <= EPSILON) {
    return fanDirection(axis, index);
  }
  return [perp[0] / length, perp[1] / length, perp[2] / length];
}

// Resolve a document into a per-record plan. `records` are display records with
// { partId, partBounds, mesh }. Returns entries carrying translation/rotation
// contributions plus per-group trail anchors for the moving line overlays.
export function compileExplodedView(THREE, document, records = [], modelBounds = null) {
  const doc = normalizeExplodedViewDocument(document);
  const explodable = (Array.isArray(records) ? records : []).filter(recordCanExplode);
  const stepCount = doc.steps.length;
  const empty = { order: doc.order, stepCount, entries: [], records: explodable, groups: [], trails: doc.trails };
  if (!THREE?.Vector3 || !THREE?.Matrix4 || !stepCount || !explodable.length) {
    return empty;
  }

  const modelCenter = boundsCenterArray(modelBounds, [0, 0, 0]);
  const contributionsByRecord = new Map();
  const groups = [];

  doc.steps.forEach((step, stepIndex) => {
    const targets = step.targets.map((target) => ({ target, segs: targetSegments(target) }))
      .filter((entry) => entry.segs.length);
    if (!targets.length) {
      return;
    }
    // Assign each record to the single most-specific matching target within this
    // step, so a record matched by both a sub-assembly and a descendant target
    // is moved once (not once per matching target).
    const recordsByTarget = new Map();
    for (const record of explodable) {
      let best = null;
      for (const targetEntry of targets) {
        if (targetMatchesRecord(targetEntry.segs, record.partId)
          && (!best || targetEntry.segs.length > best.segs.length)) {
          best = targetEntry;
        }
      }
      if (!best) {
        continue;
      }
      const bucket = recordsByTarget.get(best.target) || { targetEntry: best, records: [] };
      bucket.records.push(record);
      recordsByTarget.set(best.target, bucket);
    }

    // Sub-assembly targets move rigidly; radial resolves a per-group direction.
    let targetIndex = 0;
    for (const { targetEntry, records } of recordsByTarget.values()) {
      const groupBounds = mergeBounds(records.map(recordBounds));
      const groupCenter = boundsCenterArray(groupBounds, modelCenter);
      let contribution;
      if (step.type === "rotate") {
        contribution = { stepIndex, kind: "rotate", axis: step.axis, origin: step.origin, angleDeg: step.angleDeg };
      } else {
        const direction = step.type === "radial"
          ? radialDirection(toVectorArray(step.center, modelCenter), step.axis, groupCenter, targetIndex)
          : step.axis;
        const translation = [direction[0] * step.distance, direction[1] * step.distance, direction[2] * step.distance];
        contribution = { stepIndex, kind: "translate", translation };
        if (Math.hypot(translation[0], translation[1], translation[2]) > EPSILON) {
          groups.push({ stepIndex, key: `${step.id}:${targetEntry.target}`, from: groupCenter, translation });
        }
      }
      for (const record of records) {
        const list = contributionsByRecord.get(record) || [];
        list.push(contribution);
        contributionsByRecord.set(record, list);
      }
      targetIndex += 1;
    }
  });

  const entries = [];
  for (const [record, contributions] of contributionsByRecord) {
    contributions.sort((left, right) => left.stepIndex - right.stepIndex);
    entries.push({ record, partId: String(record.partId || "").trim(), contributions });
  }

  return { order: doc.order, stepCount, entries, records: explodable, groups, trails: doc.trails };
}

// -- application ------------------------------------------------------------

function rotationMatrix(THREE, axisArray, origin, angleRad) {
  const axis = new THREE.Vector3(axisArray[0], axisArray[1], axisArray[2]);
  if (axis.lengthSq() <= EPSILON) {
    return new THREE.Matrix4();
  }
  axis.normalize();
  const rotation = new THREE.Matrix4().makeRotationAxis(axis, angleRad);
  const toOrigin = new THREE.Matrix4().makeTranslation(-origin[0], -origin[1], -origin[2]);
  const fromOrigin = new THREE.Matrix4().makeTranslation(origin[0], origin[1], origin[2]);
  return fromOrigin.multiply(rotation).multiply(toOrigin);
}

function entryMatrix(THREE, entry, order, stepCount, progress) {
  let matrix = null;
  for (const contribution of entry.contributions) {
    const stepProgress = explodedViewStepProgress(order, contribution.stepIndex, stepCount, progress);
    if (stepProgress <= EPSILON) {
      continue;
    }
    let contributionMatrix;
    if (contribution.kind === "rotate") {
      const angleRad = (contribution.angleDeg * Math.PI) / 180 * stepProgress;
      if (Math.abs(angleRad) <= EPSILON) {
        continue;
      }
      contributionMatrix = rotationMatrix(THREE, contribution.axis, contribution.origin, angleRad);
    } else {
      const t = contribution.translation;
      if (Math.hypot(t[0], t[1], t[2]) * stepProgress <= EPSILON) {
        continue; // zero-magnitude move: leave the record cleared (null contract)
      }
      contributionMatrix = new THREE.Matrix4().makeTranslation(
        t[0] * stepProgress,
        t[1] * stepProgress,
        t[2] * stepProgress
      );
    }
    matrix = matrix ? new THREE.Matrix4().multiplyMatrices(contributionMatrix, matrix) : contributionMatrix;
  }
  return matrix;
}

// Write record.explodedViewMatrix for every compiled record at `progress`.
// Records with no active contribution are explicitly cleared to null so stale
// offsets never persist (the matrix is the sole downstream contract).
export function applyExplodedViewProgress(THREE, compiled, progress = 1) {
  if (!THREE?.Matrix4 || !compiled) {
    return;
  }
  const moved = new Set();
  for (const entry of compiled.entries || []) {
    const record = entry?.record;
    if (!record) {
      continue;
    }
    const matrix = entryMatrix(THREE, entry, compiled.order, compiled.stepCount, progress);
    record.explodedViewMatrix = matrix || null;
    moved.add(record);
  }
  for (const record of compiled.records || []) {
    if (record && !moved.has(record)) {
      record.explodedViewMatrix = null;
    }
  }
}

export function clearExplodedViewRecords(records = []) {
  for (const record of Array.isArray(records) ? records : []) {
    if (record) {
      record.explodedViewMatrix = null;
    }
  }
}

// -- derived outputs (bounds, trails) --------------------------------------

// Bounds of the assembly at a given scrub progress, for auto-framing. Transform
// each moved record's partBounds corners by its matrix and merge with the rest.
export function explodedViewBounds(THREE, compiled, fallbackBounds = null, progress = 1) {
  if (!THREE?.Vector3 || !compiled) {
    return fallbackBounds;
  }
  const matrixByRecord = new Map();
  for (const entry of compiled.entries || []) {
    const matrix = entryMatrix(THREE, entry, compiled.order, compiled.stepCount, progress);
    if (matrix) {
      matrixByRecord.set(entry.record, matrix);
    }
  }
  const boundsList = [];
  for (const record of compiled.records || []) {
    const bounds = recordBounds(record);
    if (!bounds) {
      continue;
    }
    const matrix = matrixByRecord.get(record);
    if (!matrix) {
      boundsList.push(bounds);
      continue;
    }
    const min = [Infinity, Infinity, Infinity];
    const max = [-Infinity, -Infinity, -Infinity];
    const corner = new THREE.Vector3();
    for (let i = 0; i < 8; i += 1) {
      corner.set(
        (i & 1) ? toNumber(bounds.max[0]) : toNumber(bounds.min[0]),
        (i & 2) ? toNumber(bounds.max[1]) : toNumber(bounds.min[1]),
        (i & 4) ? toNumber(bounds.max[2]) : toNumber(bounds.min[2])
      ).applyMatrix4(matrix);
      for (let axis = 0; axis < 3; axis += 1) {
        const value = corner.getComponent(axis);
        min[axis] = Math.min(min[axis], value);
        max[axis] = Math.max(max[axis], value);
      }
    }
    boundsList.push({ min, max });
  }
  return mergeBounds(boundsList) || fallbackBounds;
}

// Group-level trail segments (assembled -> current position) at `progress`, for
// the explode-line overlay. Rotate steps do not contribute straight trails.
export function explodedViewTrailSegments(THREE, compiled, progress = 1) {
  if (!compiled?.groups?.length) {
    return [];
  }
  const segments = [];
  for (const group of compiled.groups) {
    const stepProgress = explodedViewStepProgress(compiled.order, group.stepIndex, compiled.stepCount, progress);
    if (stepProgress <= EPSILON) {
      continue;
    }
    const from = group.from;
    const to = [
      from[0] + group.translation[0] * stepProgress,
      from[1] + group.translation[1] * stepProgress,
      from[2] + group.translation[2] * stepProgress
    ];
    segments.push({ key: group.key, from: [...from], to });
  }
  return segments;
}

export function easeExplodedViewProgress(value) {
  const amount = clamp(toNumber(value), 0, 1);
  return 1 - (1 - amount) ** 3;
}
