import { Vector3 } from "three";

export const MEASURE_RULER_COLOR = "#3b82f6";
export const MEASURE_RULER_HALO = "rgba(59, 130, 246, 0.32)";
export const MEASURE_RULER_ANCHOR_RADIUS = 4.5;
export const MEASURE_RULER_END_RADIUS = 4.5;
export const MEASURE_RULER_LINE_WIDTH = 2.5;
export const MEASURE_RULER_HALO_WIDTH = 6.5;
export const MEASURE_RULER_PULSE_AMPLITUDE = 1.6;
export const MEASURE_RULER_PULSE_PERIOD_MS = 220;

export function projectWorldPointToClient(point, camera, rect) {
  if (!Array.isArray(point) || point.length < 3 || !camera?.matrixWorldInverse || !camera?.projectionMatrix) {
    return null;
  }
  const x = Number(point[0]);
  const y = Number(point[1]);
  const z = Number(point[2]);
  if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) {
    return null;
  }
  const projected = new Vector3(x, y, z)
    .applyMatrix4(camera.matrixWorldInverse)
    .applyMatrix4(camera.projectionMatrix);
  if (!Number.isFinite(projected.x) || !Number.isFinite(projected.y) || projected.z > 1) {
    return null;
  }
  const left = Number(rect?.left) || 0;
  const top = Number(rect?.top) || 0;
  const width = Number(rect?.width) || 1;
  const height = Number(rect?.height) || 1;
  return {
    x: left + ((projected.x + 1) * 0.5 * width),
    y: top + ((1 - projected.y) * 0.5 * height)
  };
}

function readoutNow(now) {
  if (Number.isFinite(now)) {
    return now;
  }
  return typeof performance !== "undefined" && typeof performance.now === "function"
    ? performance.now()
    : Date.now();
}

function drawCircle(context, x, y, radius, { fill = null, stroke = null, lineWidth = 2 } = {}) {
  context.beginPath();
  context.arc(x, y, radius, 0, Math.PI * 2);
  if (fill) {
    context.fillStyle = fill;
    context.fill();
  }
  if (stroke) {
    context.strokeStyle = stroke;
    context.lineWidth = lineWidth;
    context.stroke();
  }
}

export function redrawMeasureRuler(canvas, {
  anchorPoint = null,
  endPoint = null,
  camera = null,
  rect = null,
  pulse = false,
  now = null
} = {}) {
  if (!canvas) {
    return;
  }
  const context = canvas.getContext("2d");
  if (!context) {
    return;
  }
  const width = canvas.width || 1;
  const height = canvas.height || 1;
  context.clearRect(0, 0, width, height);

  const anchor = projectWorldPointToClient(anchorPoint, camera, rect);
  if (!anchor) {
    return;
  }
  const end = endPoint ? projectWorldPointToClient(endPoint, camera, rect) : null;

  if (end) {
    context.lineCap = "round";
    context.strokeStyle = MEASURE_RULER_HALO;
    context.lineWidth = MEASURE_RULER_HALO_WIDTH;
    context.beginPath();
    context.moveTo(anchor.x, anchor.y);
    context.lineTo(end.x, end.y);
    context.stroke();
    context.strokeStyle = MEASURE_RULER_COLOR;
    context.lineWidth = MEASURE_RULER_LINE_WIDTH;
    context.beginPath();
    context.moveTo(anchor.x, anchor.y);
    context.lineTo(end.x, end.y);
    context.stroke();
  }

  drawCircle(context, anchor.x, anchor.y, MEASURE_RULER_ANCHOR_RADIUS, {
    fill: MEASURE_RULER_COLOR,
    stroke: "#ffffff",
    lineWidth: 2
  });

  if (end) {
    if (pulse) {
      const pulseRadius = MEASURE_RULER_END_RADIUS + 2 +
        Math.abs(Math.sin(readoutNow(now) / MEASURE_RULER_PULSE_PERIOD_MS)) * MEASURE_RULER_PULSE_AMPLITUDE;
      drawCircle(context, end.x, end.y, pulseRadius, { stroke: MEASURE_RULER_HALO, lineWidth: 2 });
    }
    drawCircle(context, end.x, end.y, MEASURE_RULER_END_RADIUS, {
      fill: "#ffffff",
      stroke: MEASURE_RULER_COLOR,
      lineWidth: 2
    });
  }
}
