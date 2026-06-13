export const ORBIT_BASE_PAN_SPEED = 1.35;

const MIN_PAN_ZOOM = 0.25;
const MAX_PAN_ZOOM = 16;

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function positiveNumber(value, fallback) {
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric > 0 ? numeric : fallback;
}

export function orbitPanZoomScale(camera) {
  return Math.sqrt(clamp(positiveNumber(camera?.zoom, 1), MIN_PAN_ZOOM, MAX_PAN_ZOOM));
}

export function orbitPanSpeedForCamera(camera, {
  basePanSpeed = ORBIT_BASE_PAN_SPEED
} = {}) {
  return positiveNumber(basePanSpeed, ORBIT_BASE_PAN_SPEED) * orbitPanZoomScale(camera);
}

export function syncOrbitPanSpeed(controls, camera, options = {}) {
  if (!controls) {
    return 0;
  }
  const speed = orbitPanSpeedForCamera(camera || controls.object, options);
  controls.panSpeed = speed;
  return speed;
}
