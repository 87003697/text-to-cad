function isOrthographicNavigation(projection, camera) {
  return projection === "orthographic" || camera?.isOrthographicCamera === true;
}

function assignIfDefined(target, key, value) {
  if (value === undefined) {
    return;
  }
  target[key] = value;
}

export function syncOrbitNavigationMode(controls, camera, {
  projection = "",
  mouse,
  touch
} = {}) {
  if (!controls) {
    return false;
  }

  const useOrthographicNavigation = isOrthographicNavigation(projection, camera || controls.object);
  const mouseButtons = controls.mouseButtons || {};
  const touches = controls.touches || {};

  assignIfDefined(mouseButtons, "LEFT", useOrthographicNavigation ? mouse?.PAN : mouse?.ROTATE);
  assignIfDefined(mouseButtons, "MIDDLE", mouse?.DOLLY);
  assignIfDefined(mouseButtons, "RIGHT", useOrthographicNavigation ? mouse?.ROTATE : mouse?.PAN);
  assignIfDefined(touches, "ONE", useOrthographicNavigation ? touch?.PAN : touch?.ROTATE);
  assignIfDefined(touches, "TWO", touch?.DOLLY_PAN);

  controls.mouseButtons = mouseButtons;
  controls.touches = touches;
  return true;
}
