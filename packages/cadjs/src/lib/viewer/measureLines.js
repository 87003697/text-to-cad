import { createScreenSpaceLineSegments } from "../../common/renderEdges.js";
import { isFinitePoint } from "./measurement.js";

export const MEASURE_LINE_COLOR = "#3b82f6";
export const MEASURE_LINE_WIDTH = 2.2;
export const MEASURE_LINE_OPACITY = 0.95;
export const MEASURE_LINE_DRAFT_OPACITY = 0.55;
export const MEASURE_LINE_DRAFT_WIDTH = 1.8;
export const MEASURE_LINE_DRAFT_ID = "draft";
export const MEASURE_LINE_RENDER_ORDER = 27;

export function createMeasureLineSegments(runtime, state, { materials = null } = {}) {
  if (!runtime?.THREE) {
    return { committed: null, draft: null };
  }
  const segments = measureRulerLineSegments(state);
  const committedPoints = [];
  let draftPoints = null;
  for (const segment of segments) {
    if (segment.committed) {
      committedPoints.push(segment.start[0], segment.start[1], segment.start[2], segment.end[0], segment.end[1], segment.end[2]);
    } else {
      draftPoints = [segment.start[0], segment.start[1], segment.start[2], segment.end[0], segment.end[1], segment.end[2]];
    }
  }
  const options = {
    color: MEASURE_LINE_COLOR,
    depthTest: false,
    depthWrite: false,
    renderOrder: MEASURE_LINE_RENDER_ORDER
  };
  return {
    committed: committedPoints.length
      ? createScreenSpaceLineSegments(runtime, committedPoints, {
        ...options,
        opacity: MEASURE_LINE_OPACITY,
        lineWidth: MEASURE_LINE_WIDTH
      }, materials)
      : null,
    draft: draftPoints
      ? createScreenSpaceLineSegments(runtime, draftPoints, {
        ...options,
        opacity: MEASURE_LINE_DRAFT_OPACITY,
        lineWidth: MEASURE_LINE_DRAFT_WIDTH
      }, materials)
      : null
  };
}

export function clearMeasureLineGroup(runtime, group) {
  if (!group) {
    return;
  }
  while (group.children?.length) {
    const child = group.children[0];
    group.remove(child);
    disposeMeasureLineChild(child);
  }
  group.visible = false;
}

function disposeMeasureLineChild(child) {
  while (child.children?.length) {
    const nested = child.children[0];
    child.remove(nested);
    disposeMeasureLineChild(nested);
  }
  if (typeof child.userData?.beforeDispose === "function") {
    child.userData.beforeDispose(child);
    delete child.userData.beforeDispose;
  }
  if (child.userData?.disposeGeometry !== false) {
    child.geometry?.dispose?.();
  }
  if (child.userData?.disposeMaterial !== false) {
    const materials = Array.isArray(child.material) ? child.material : [child.material];
    for (const material of materials) {
      material?.dispose?.();
    }
  }
}

export function measureRulerLineSegments(state) {
  if (!state) {
    return [];
  }
  const segments = [];
  for (const item of state.measurements || []) {
    const start = item?.pickA?.point;
    const end = item?.pickB?.point;
    if (isFinitePoint(start) && isFinitePoint(end)) {
      segments.push({
        id: item.id,
        start,
        end,
        committed: true
      });
    }
  }
  const draft = state.draft;
  const draftEnd = draft?.hover?.point || null;
  if (isFinitePoint(draft?.anchor?.point) && isFinitePoint(draftEnd)) {
    segments.push({
      id: MEASURE_LINE_DRAFT_ID,
      start: draft.anchor.point,
      end: draftEnd,
      committed: false
    });
  }
  return segments;
}

export function midpointOfPoints(a, b) {
  if (!isFinitePoint(a) || !isFinitePoint(b)) {
    return null;
  }
  return [
    (a[0] + b[0]) / 2,
    (a[1] + b[1]) / 2,
    (a[2] + b[2]) / 2
  ];
}

export function clampMeasureChipPosition(position, bounds, { width = 180, height = 44 } = {}) {
  if (!position || !bounds || !(Number(bounds.width) > 0) || !(Number(bounds.height) > 0)) {
    return null;
  }
  const viewportWidth = Number(bounds.width);
  const viewportHeight = Number(bounds.height);
  const halfWidth = Number(width) / 2;
  const halfHeight = Number(height) / 2;
  const minX = halfWidth;
  const maxX = Math.max(minX, viewportWidth - halfWidth);
  const minY = halfHeight;
  const maxY = Math.max(minY, viewportHeight - halfHeight);
  return {
    x: Math.min(Math.max(Number(position.x) || 0, minX), maxX),
    y: Math.min(Math.max(Number(position.y) || 0, minY), maxY)
  };
}
