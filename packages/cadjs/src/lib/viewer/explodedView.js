// Auto-explode generator: turn display records + bounds + hints into an ordered
// explode-step *document* (see explodedViewSteps.js for the model/evaluator).
//
// This replaces the former global-heuristic solver. It still reads the
// occurrence tree and per-part bounds (no mate/contact data is available at view
// time), but emits editable per-group steps and fixes the wonky behaviours of
// the old layout:
//   - principal explode axis is detected from the component layout ("auto"),
//     not hardcoded to Z;
//   - coplanar groups separate laterally instead of serializing into a stack;
//   - coaxial groups explode along the axis instead of scattering sideways;
//   - distances are absolute model units, swept so groups clear one another.
//
// The generated steps are ordinary steps the user can edit, reorder, or delete.

import {
  EPSILON,
  MAX_EXPLODED_VIEW_DEPTH,
  boundsCenterArray,
  boundsRadius,
  boundsSize,
  clamp,
  mergeBounds,
  normalizeExplodeAutoHints,
  normalizeExplodedViewDocument,
  occurrenceSegments,
  recordBounds,
  recordCanExplode,
  toNumber
} from "./explodedViewSteps.js";

const AXIS_VECTORS = Object.freeze({
  x: [1, 0, 0],
  y: [0, 1, 0],
  z: [0, 0, 1]
});

function commonOccurrencePrefix(records = []) {
  const paths = records
    .map((record) => occurrenceSegments(record?.partId))
    .filter((segments) => segments.length > 1);
  if (!paths.length) {
    return [];
  }
  const prefix = [];
  const shortest = Math.min(...paths.map((segments) => segments.length));
  for (let index = 0; index < shortest; index += 1) {
    const value = paths[0][index];
    if (!paths.every((segments) => segments[index] === value)) {
      break;
    }
    prefix.push(value);
  }
  return prefix.length >= shortest ? prefix.slice(0, -1) : prefix;
}

// Occurrence-path prefix that groups a record at the requested depth below the
// assembly's common prefix, so sub-assemblies move rigidly at depth 1 and split
// into deeper components as depth grows.
export function explodedViewGroupKey(partId, { depth = 1, commonPrefix = [] } = {}) {
  const text = String(partId || "").trim();
  const segments = occurrenceSegments(text);
  if (!segments.length) {
    return text;
  }
  const safeDepth = Math.max(1, Math.min(Math.round(Number(depth)) || 1, MAX_EXPLODED_VIEW_DEPTH));
  const prefixLength = Math.min(commonPrefix.length, Math.max(segments.length - 1, 0));
  const groupLength = Math.min(segments.length, prefixLength + safeDepth);
  return segments.slice(0, groupLength).join(".") || text;
}

function groupRecords(records, depth) {
  const commonPrefix = commonOccurrencePrefix(records);
  const groupsByKey = new Map();
  records.forEach((record, recordIndex) => {
    const groupKey = explodedViewGroupKey(record?.partId, { depth, commonPrefix })
      || String(record?.partId || `part:${recordIndex}`);
    const existing = groupsByKey.get(groupKey) || {
      key: groupKey,
      records: [],
      recordIndex,
      boundsList: []
    };
    existing.records.push(record);
    const bounds = recordBounds(record);
    if (bounds) {
      existing.boundsList.push(bounds);
    }
    groupsByKey.set(groupKey, existing);
  });
  return [...groupsByKey.values()].map((group) => {
    const bounds = mergeBounds(group.boundsList) || recordBounds(group.records[0]) || null;
    return {
      key: group.key,
      records: group.records,
      recordIndex: group.recordIndex,
      bounds,
      center: boundsCenterArray(bounds, [0, 0, 0]),
      radius: boundsRadius(bounds)
    };
  });
}

// Choose the explode axis. World axes come straight through; "auto" picks the
// world axis with the greatest spread of group centers (robust, axis-aligned
// analogue of PCA snapped to a principal axis), tie-broken toward Z.
function resolveExplodeAxisIndex(mode, groups) {
  if (mode === "x") return 0;
  if (mode === "y") return 1;
  if (mode === "z") return 2;
  const centers = groups.map((group) => group.center);
  const mean = [0, 1, 2].map((axis) => centers.reduce((sum, c) => sum + c[axis], 0) / (centers.length || 1));
  const variance = [0, 1, 2].map(
    (axis) => centers.reduce((sum, c) => sum + (c[axis] - mean[axis]) ** 2, 0)
  );
  let best = 2;
  let bestValue = -Infinity;
  for (const axis of [0, 1, 2]) {
    // Bias ties toward Z (the usual CAD stacking axis) with a tiny nudge.
    const value = variance[axis] + (axis === 2 ? EPSILON : 0);
    if (value > bestValue) {
      bestValue = value;
      best = axis;
    }
  }
  return best;
}

function scaleVec(vec, scalar) {
  return [vec[0] * scalar, vec[1] * scalar, vec[2] * scalar];
}

function addVec(a, b) {
  return [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
}

function vecLength(vec) {
  return Math.hypot(vec[0], vec[1], vec[2]);
}

// Build layers of groups that overlap along the explode axis (coplanar layers),
// sorted from the base outward.
function buildLayers(groups, axisIndex, tolerance) {
  const sorted = [...groups].sort((left, right) => {
    const delta = left.center[axisIndex] - right.center[axisIndex];
    return Math.abs(delta) > EPSILON ? delta : left.recordIndex - right.recordIndex;
  });
  const layers = [];
  for (const group of sorted) {
    const previous = layers[layers.length - 1];
    if (previous && Math.abs(group.center[axisIndex] - previous.center) <= tolerance) {
      previous.groups.push(group);
      previous.bounds = mergeBounds([previous.bounds, group.bounds]) || previous.bounds;
      previous.center = boundsCenterArray(previous.bounds)[axisIndex];
    } else {
      layers.push({ center: group.center[axisIndex], bounds: group.bounds, groups: [group] });
    }
  }
  return layers;
}

// Lateral (perpendicular-to-axis) unit direction for a coplanar group, snapped
// to the stronger of the two perpendicular world axes; fans by index when the
// group sits on the axis (coaxial, no intrinsic side).
function lateralDirection(group, layerCenter, axisIndex, index) {
  const perpAxes = [0, 1, 2].filter((axis) => axis !== axisIndex);
  const delta = [
    group.center[0] - layerCenter[0],
    group.center[1] - layerCenter[1],
    group.center[2] - layerCenter[2]
  ];
  delta[axisIndex] = 0;
  const [pa, pb] = perpAxes;
  if (Math.hypot(delta[pa], delta[pb]) <= EPSILON) {
    // Coaxial: fan alternately along the two perpendicular axes.
    const axis = perpAxes[index % 2];
    const sign = (Math.floor(index / 2) % 2 === 0) ? 1 : -1;
    const dir = [0, 0, 0];
    dir[axis] = sign;
    return dir;
  }
  const dominant = Math.abs(delta[pa]) >= Math.abs(delta[pb]) ? pa : pb;
  const dir = [0, 0, 0];
  dir[dominant] = delta[dominant] >= 0 ? 1 : -1;
  return dir;
}

function generateRadialSteps(groups, modelBounds, hints) {
  const modelCenter = boundsCenterArray(modelBounds, [0, 0, 0]);
  const axisVec = AXIS_VECTORS.z;
  const modelRadiusXY = Math.max(
    Math.hypot(boundsSize(modelBounds, 0), boundsSize(modelBounds, 1)) / 2,
    EPSILON
  );
  const modelRadius = Math.max(boundsRadius(modelBounds), EPSILON);
  const baseDistance = Math.max(modelRadiusXY * 0.85 * hints.gapScale, modelRadius * 0.28);
  const sign = hints.direction === "negative" ? -1 : 1;
  const steps = [];
  const sorted = [...groups].sort((left, right) => left.recordIndex - right.recordIndex);
  sorted.forEach((group, index) => {
    const distance = (baseDistance + group.radius * 0.6 * hints.gapScale) * sign;
    if (Math.abs(distance) <= EPSILON) {
      return;
    }
    steps.push({
      id: `s${index + 1}`,
      type: "radial",
      targets: [group.key],
      axis: axisVec,
      center: modelCenter,
      distance
    });
  });
  return steps;
}

function generateAxialSteps(groups, modelBounds, axisIndex, hints) {
  const modelSpan = Math.max(boundsSize(modelBounds, axisIndex), EPSILON);
  const modelRadius = Math.max(boundsRadius(modelBounds), EPSILON);
  const axisVec = AXIS_VECTORS[["x", "y", "z"][axisIndex]];
  const sign = hints.direction === "negative" ? -1 : 1;

  const thicknesses = groups
    .map((group) => boundsSize(group.bounds, axisIndex))
    .filter((value) => value > EPSILON)
    .sort((left, right) => left - right);
  const medianThickness = thicknesses.length
    ? thicknesses[Math.floor(thicknesses.length / 2)]
    : modelSpan * 0.1;
  const tolerance = Math.max(modelSpan * 0.025, medianThickness * 0.18, EPSILON);
  const minimumGap = Math.max(modelSpan * 0.075, medianThickness * 0.35, modelRadius * 0.035, EPSILON)
    * hints.gapScale;

  const layers = buildLayers(groups, axisIndex, tolerance);

  // Axial offset per layer: spread outward from the base with a real gap. Each
  // successive layer moves at least `minimumGap` further than the previous one
  // (so already-separated parts still visibly separate) and never overlaps the
  // previous exploded extent (so thick parts get extra room).
  const layerOffset = new Map();
  let previousExplodedMax = null;
  let previousOffset = 0;
  layers.forEach((layer, layerIndex) => {
    const layerMin = toNumber(layer.bounds.min[axisIndex]);
    const layerMax = toNumber(layer.bounds.max[axisIndex]);
    let offset = 0;
    if (layerIndex === 0) {
      offset = 0; // base layer stays put (grounds the explode)
    } else {
      const clearance = previousExplodedMax + minimumGap - layerMin;
      offset = Math.max(clearance, previousOffset + minimumGap);
    }
    previousOffset = offset;
    previousExplodedMax = layerMax + offset;
    layerOffset.set(layer, offset);
  });

  const steps = [];
  let stepIndex = 0;
  layers.forEach((layer) => {
    const axialOffset = layerOffset.get(layer) * sign;
    const layerCenter = boundsCenterArray(layer.bounds, [0, 0, 0]);
    const multiGroup = layer.groups.length > 1;
    layer.groups.forEach((group, groupIndexInLayer) => {
      let displacement = scaleVec(axisVec, axialOffset);
      if (multiGroup) {
        // Coplanar groups: separate laterally so they don't stack into a column.
        const lateralDir = lateralDirection(group, layerCenter, axisIndex, groupIndexInLayer);
        const layerLateralRadius = Math.max(
          boundsSize(layer.bounds, ([0, 1, 2].filter((a) => a !== axisIndex))[0]),
          boundsSize(layer.bounds, ([0, 1, 2].filter((a) => a !== axisIndex))[1])
        ) / 2;
        const lateralDistance = (layerLateralRadius + group.radius * 0.75 + minimumGap) * hints.gapScale;
        displacement = addVec(displacement, scaleVec(lateralDir, lateralDistance));
      }
      const distance = vecLength(displacement);
      stepIndex += 1;
      if (distance <= EPSILON) {
        return;
      }
      steps.push({
        id: `s${stepIndex}`,
        type: "translate",
        targets: [group.key],
        axis: [displacement[0] / distance, displacement[1] / distance, displacement[2] / distance],
        distance
      });
    });
  });
  return steps;
}

// Generate a normalized explode document from records + bounds + hints.
export function generateExplodedViewDocument(THREE, records = [], modelBounds = null, hints = {}, options = {}) {
  const autoHints = normalizeExplodeAutoHints(hints);
  const explodable = (Array.isArray(records) ? records : []).filter(recordCanExplode);
  const base = {
    enabled: options.enabled !== undefined ? options.enabled : true,
    amount: options.amount,
    order: options.order,
    trails: options.trails,
    auto: autoHints,
    steps: []
  };
  if (explodable.length < 2) {
    return normalizeExplodedViewDocument(base);
  }
  const groups = groupRecords(explodable, autoHints.depth).filter((group) => group.records.length && group.bounds);
  if (groups.length < 2) {
    return normalizeExplodedViewDocument(base);
  }

  let steps;
  if (autoHints.mode === "radial") {
    steps = generateRadialSteps(groups, modelBounds, autoHints);
  } else {
    const axisIndex = resolveExplodeAxisIndex(autoHints.mode, groups);
    steps = generateAxialSteps(groups, modelBounds, axisIndex, autoHints);
  }

  return normalizeExplodedViewDocument({ ...base, steps });
}
