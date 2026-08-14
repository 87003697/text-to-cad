// THROWAWAY closed Render Program request contract shared by client and tests.
const SCHEMA = "meshshot.browser-sidecar.render-request/2";
const PROGRAMS = new Set(["suite", "probe", "viewer", "residual", "hold"]);
const DIRECTIONS = new Set(["-x", "+x", "-y", "+y", "-z", "+z"]);

function object(value, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} must be an object`);
  }
  return value;
}

function exactKeys(value, keys, label) {
  object(value, label);
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    throw new Error(`${label} requires exact keys: ${expected.join(",")}`);
  }
}

function geometry(value, label) {
  exactKeys(value, ["vertices", "faces"], label);
  const { vertices, faces } = value;
  if (!Array.isArray(vertices) || !vertices.length || vertices.length > 10_000 ||
      vertices.some((vertex) => !Array.isArray(vertex) || vertex.length !== 3 ||
        vertex.some((coordinate) => typeof coordinate !== "number" || !Number.isFinite(coordinate)))) {
    throw new Error(`${label} geometry requires bounded finite 3D vertices`);
  }
  if (!Array.isArray(faces) || !faces.length || faces.length > 20_000 ||
      faces.some((face) => !Array.isArray(face) || face.length !== 3 ||
        face.some((index) => !Number.isInteger(index) || index < 0 || index >= vertices.length))) {
    throw new Error(`${label} geometry requires bounded valid triangle indices`);
  }
}

function residualPayload(payload) {
  exactKeys(payload, ["reference", "candidate", "variant", "exteriorDirections", "options"], "residual payload");
  geometry(payload.reference, "reference");
  geometry(payload.candidate, "candidate");
  if (!new Set(["step", "final"]).has(payload.variant)) {
    throw new Error("residual variant must be step or final");
  }
  if (!Array.isArray(payload.exteriorDirections) ||
      new Set(payload.exteriorDirections).size !== payload.exteriorDirections.length ||
      payload.exteriorDirections.some((direction) => !DIRECTIONS.has(direction))) {
    throw new Error("exteriorDirections must be unique signed x/y/z values");
  }
  exactKeys(payload.options, ["cameraPolicy", "canonicalPostprocess"], "residual options");
  if (payload.options.cameraPolicy !== "profile-fixed") {
    throw new Error("cameraPolicy must be profile-fixed");
  }
  if (payload.options.canonicalPostprocess !== true) {
    throw new Error("canonicalPostprocess must be true");
  }
}

function viewerPayload(payload) {
  exactKeys(payload, ["modelKey", "inspectionControl"], "viewer payload");
  if (payload.modelKey !== "cube-stl" || payload.inspectionControl !== "toggle-projection") {
    throw new Error("viewer accepts only cube-stl and toggle-projection");
  }
}

function holdPayload(payload) {
  exactKeys(payload, ["model", "ownMarker", "peerMarker"], "hold payload");
  geometry(payload.model, "hold model");
  const marker = /^[a-z0-9][a-z0-9-]{0,47}$/;
  if (!marker.test(payload.ownMarker) || !marker.test(payload.peerMarker) || payload.ownMarker === payload.peerMarker) {
    throw new Error("hold markers must be distinct bounded lowercase identifiers");
  }
}

export function parseRequest(raw) {
  if (typeof raw !== "string" || Buffer.byteLength(raw, "utf8") > 1_048_576) {
    throw new Error("request must be at most 1 MiB of JSON");
  }
  const request = JSON.parse(raw);
  exactKeys(request, ["schema", "program", "payload"], "request");
  if (request.schema !== SCHEMA || !PROGRAMS.has(request.program)) {
    throw new Error("request schema/program is not registered");
  }
  if (request.program === "suite") {
    exactKeys(request.payload, ["viewer", "residual"], "suite payload");
    viewerPayload(request.payload.viewer);
    residualPayload(request.payload.residual);
  } else if (request.program === "viewer") {
    viewerPayload(request.payload);
  } else if (request.program === "residual") {
    residualPayload(request.payload);
  } else if (request.program === "hold") {
    holdPayload(request.payload);
  } else {
    exactKeys(request.payload, [], "probe payload");
  }
  return request;
}
