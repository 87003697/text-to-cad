import * as THREE from "three";

const ORIGIN = new THREE.Vector3(0, 0, 0);
const DIRECTION_VECTOR = Object.freeze({
  "-x": [-1, 0, 0],
  "+x": [1, 0, 0],
  "-y": [0, -1, 0],
  "+y": [0, 1, 0],
  "-z": [0, 0, -1],
  "+z": [0, 0, 1],
});

function decodeBase64Bytes(value) {
  const decoded = atob(value);
  const bytes = new Uint8Array(decoded.length);
  for (let index = 0; index < decoded.length; index += 1) {
    bytes[index] = decoded.charCodeAt(index);
  }
  return bytes;
}

function decodeFloat32Le(value, count) {
  const bytes = decodeBase64Bytes(value);
  if (bytes.byteLength !== count * 4) throw new Error("invalid packed positions");
  const values = new Float32Array(count);
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  for (let index = 0; index < count; index += 1) {
    values[index] = view.getFloat32(index * 4, true);
  }
  return values;
}

function decodeUint32Le(value, count) {
  const bytes = decodeBase64Bytes(value);
  if (bytes.byteLength !== count * 4) throw new Error("invalid packed indices");
  const values = new Uint32Array(count);
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  for (let index = 0; index < count; index += 1) {
    values[index] = view.getUint32(index * 4, true);
  }
  return values;
}

function geometryFromPayload(payload) {
  if (payload.schema !== "text-to-cad.packed-triangle-mesh/1") {
    throw new Error("unsupported packed geometry");
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute(
    "position",
    new THREE.BufferAttribute(
      decodeFloat32Le(payload.positionsF32LeBase64, payload.vertexCount * 3),
      3,
    ),
  );
  geometry.setIndex(
    new THREE.BufferAttribute(
      decodeUint32Le(payload.indicesU32LeBase64, payload.faceCount * 3),
      1,
    ),
  );
  geometry.computeVertexNormals();
  geometry.computeBoundingSphere();
  return geometry;
}

function cameraForView(view, profile, aspect = 1) {
  const direction = new THREE.Vector3(...view.direction).normalize();
  const up = new THREE.Vector3(...view.up).normalize();
  let camera;
  if (view.kind === "axial_depth") {
    const config = profile.camera.orthographic;
    const half = config.half_extent;
    camera = new THREE.OrthographicCamera(
      -half * aspect,
      half * aspect,
      half,
      -half,
      config.near,
      config.far,
    );
    camera.position.copy(direction).multiplyScalar(config.position_distance);
  } else {
    const config = profile.camera.perspective;
    camera = new THREE.PerspectiveCamera(
      config.vertical_fov_degrees,
      aspect,
      config.near,
      config.far,
    );
    camera.position.copy(direction).multiplyScalar(config.position_distance);
  }
  camera.up.copy(up);
  camera.lookAt(ORIGIN);
  camera.updateProjectionMatrix();
  camera.updateMatrixWorld(true);
  return camera;
}

function axialMaterial(view, profile) {
  const far = profile.depth.farthest_intensity / 255;
  const near = profile.depth.nearest_intensity / 255;
  return new THREE.ShaderMaterial({
    side: THREE.DoubleSide,
    uniforms: {
      viewAxis: { value: new THREE.Vector3(...view.direction).normalize() },
      farIntensity: { value: far },
      nearIntensity: { value: near },
    },
    vertexShader: `
      varying vec3 canonicalPosition;
      void main() {
        canonicalPosition = position;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }
    `,
    fragmentShader: `
      varying vec3 canonicalPosition;
      uniform vec3 viewAxis;
      uniform float farIntensity;
      uniform float nearIntensity;
      void main() {
        float canonicalDepth = clamp(dot(canonicalPosition, viewAxis) + 0.5, 0.0, 1.0);
        float intensity = mix(farIntensity, nearIntensity, canonicalDepth);
        gl_FragColor = vec4(vec3(intensity), 1.0);
      }
    `,
  });
}

function shadedMaterial() {
  return new THREE.MeshLambertMaterial({
    color: 0xffffff,
    side: THREE.DoubleSide,
  });
}

function renderGeometry(renderer, target, geometry, view, profile, pixelSize) {
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x000000);
  const camera = cameraForView(view, profile);
  const material = view.kind === "axial_depth"
    ? axialMaterial(view, profile)
    : shadedMaterial();
  scene.add(new THREE.Mesh(geometry, material));
  if (view.kind === "perspective_shaded") {
    const ambient = new THREE.AmbientLight(
      0xffffff,
      profile.lighting.ambient_intensity,
    );
    const directional = new THREE.DirectionalLight(
      0xffffff,
      profile.lighting.directional_intensity,
    );
    directional.position.set(...profile.lighting.direction);
    scene.add(ambient, directional);
  }
  renderer.setRenderTarget(target);
  renderer.setViewport(0, 0, pixelSize, pixelSize);
  renderer.setScissorTest(false);
  renderer.setClearColor(0x000000, 1);
  renderer.clear(true, true, true);
  renderer.render(scene, camera);
  const rgba = new Uint8Array(pixelSize * pixelSize * 4);
  renderer.readRenderTargetPixels(target, 0, 0, pixelSize, pixelSize, rgba);
  const grayscale = new Uint8Array(pixelSize * pixelSize);
  for (let y = 0; y < pixelSize; y += 1) {
    const sourceY = pixelSize - 1 - y;
    for (let x = 0; x < pixelSize; x += 1) {
      const source = (sourceY * pixelSize + x) * 4;
      grayscale[y * pixelSize + x] = Math.max(
        rgba[source],
        rgba[source + 1],
        rgba[source + 2],
      );
    }
  }
  material.dispose();
  return grayscale;
}

function markerEdge(view, directionName) {
  const cameraDirection = new THREE.Vector3(...view.direction).normalize();
  const cameraUp = new THREE.Vector3(...view.up).normalize();
  const right = new THREE.Vector3().crossVectors(cameraUp, cameraDirection).normalize();
  const screenUp = new THREE.Vector3().crossVectors(cameraDirection, right).normalize();
  const direction = new THREE.Vector3(...DIRECTION_VECTOR[directionName]);
  let horizontal = direction.dot(right);
  const vertical = direction.dot(screenUp);
  if (view.horizontal_flip) horizontal = -horizontal;
  if (Math.abs(horizontal) < 1e-6 && Math.abs(vertical) < 1e-6) {
    return direction.dot(cameraDirection) >= 0 ? "toward" : "away";
  }
  if (Math.abs(horizontal) >= Math.abs(vertical)) return horizontal >= 0 ? "right" : "left";
  return vertical >= 0 ? "top" : "bottom";
}

function drawExteriorMarker(context, edge, size, offset) {
  const margin = Math.max(3, Math.round(size * 0.015));
  const span = Math.max(7, Math.round(size * 0.035));
  let x = size / 2 + offset;
  let y = size / 2 + offset;
  if (edge === "left") x = margin;
  if (edge === "right") x = size - margin;
  if (edge === "top" || edge === "toward") y = margin;
  if (edge === "bottom" || edge === "away") y = size - margin;
  context.save();
  context.fillStyle = "rgb(255,0,0)";
  context.strokeStyle = "rgb(255,255,255)";
  context.lineWidth = Math.max(1, Math.round(size / 252));
  context.beginPath();
  context.arc(x, y, span / 2, 0, Math.PI * 2);
  context.fill();
  context.stroke();
  if (edge === "away") {
    context.beginPath();
    context.moveTo(x - span / 3, y - span / 3);
    context.lineTo(x + span / 3, y + span / 3);
    context.moveTo(x + span / 3, y - span / 3);
    context.lineTo(x - span / 3, y + span / 3);
    context.stroke();
  }
  context.restore();
}

function annotateTile(context, view, profile, size, exteriorDirections) {
  const scale = size / profile.variants.step.tile_pixels[0];
  context.save();
  context.font = `${Math.max(11, Math.round(12 * scale))}px monospace`;
  context.textBaseline = "top";
  context.fillStyle = "rgb(255,255,255)";
  context.fillText(view.name, Math.round(7 * scale), Math.round(6 * scale));

  const legendY = size - Math.round(13 * scale);
  const legendX = Math.round(7 * scale);
  const legendSize = Math.max(3, Math.round(5 * scale));
  for (const [index, color] of ["rgb(0,255,0)", "rgb(255,0,0)", "rgb(255,255,0)"].entries()) {
    context.fillStyle = color;
    context.fillRect(legendX + index * legendSize * 2, legendY, legendSize, legendSize);
  }

  const axisOriginX = size - Math.round(18 * scale);
  const axisOriginY = size - Math.round(18 * scale);
  context.lineWidth = Math.max(1, Math.round(scale));
  for (const [color, dx, dy] of [
    ["rgb(255,0,0)", 9, 0],
    ["rgb(0,255,0)", 0, -9],
    ["rgb(0,96,255)", -6, 6],
  ]) {
    context.strokeStyle = color;
    context.beginPath();
    context.moveTo(axisOriginX, axisOriginY);
    context.lineTo(axisOriginX + dx * scale, axisOriginY + dy * scale);
    context.stroke();
  }
  context.restore();

  return exteriorDirections.map((direction, index) => {
    const edge = markerEdge(view, direction);
    drawExteriorMarker(context, edge, size, index * Math.max(9, Math.round(scale * 9)));
    return { direction, edge };
  });
}

function composeTile(reference, candidate, view, profile, pixelSize, exteriorDirections) {
  const canvas = document.createElement("canvas");
  canvas.width = pixelSize;
  canvas.height = pixelSize;
  const context = canvas.getContext("2d", { alpha: false });
  context.fillStyle = "rgb(0,0,0)";
  context.fillRect(0, 0, pixelSize, pixelSize);
  const image = context.createImageData(pixelSize, pixelSize);
  for (let y = 0; y < pixelSize; y += 1) {
    for (let x = 0; x < pixelSize; x += 1) {
      const sourceX = view.horizontal_flip ? pixelSize - 1 - x : x;
      const source = y * pixelSize + sourceX;
      const destination = (y * pixelSize + x) * 4;
      image.data[destination] = candidate[source];
      image.data[destination + 1] = reference[source];
      image.data[destination + 2] = 0;
      image.data[destination + 3] = 255;
    }
  }
  context.putImageData(image, 0, 0);
  const markers = annotateTile(context, view, profile, pixelSize, exteriorDirections);
  return { canvas, markers };
}

async function renderResidual(payload) {
  const { profile, variant, exteriorDirections = [] } = payload;
  const variantProfile = profile.variants[variant];
  if (!variantProfile) throw new Error(`unsupported render variant: ${variant}`);
  const [tileWidth, tileHeight] = variantProfile.tile_pixels;
  if (tileWidth !== tileHeight) throw new Error("meshshot requires square tiles");
  const renderSize = tileWidth * variantProfile.render_scale;
  const renderer = new THREE.WebGLRenderer({
    antialias: false,
    alpha: false,
    preserveDrawingBuffer: false,
    powerPreference: "high-performance",
  });
  renderer.outputColorSpace = THREE.LinearSRGBColorSpace;
  renderer.toneMapping = THREE.NoToneMapping;
  renderer.setPixelRatio(1);
  renderer.setSize(renderSize, renderSize, false);
  const target = new THREE.WebGLRenderTarget(renderSize, renderSize, {
    format: THREE.RGBAFormat,
    type: THREE.UnsignedByteType,
    minFilter: THREE.LinearFilter,
    magFilter: THREE.LinearFilter,
    depthBuffer: true,
    stencilBuffer: false,
  });
  target.samples = 4;
  const referenceGeometry = geometryFromPayload(payload.reference);
  const candidateGeometry = geometryFromPayload(payload.candidate);
  const [imageWidth, imageHeight] = variantProfile.image_pixels;
  const output = document.createElement("canvas");
  output.width = imageWidth;
  output.height = imageHeight;
  const outputContext = output.getContext("2d", { alpha: false });
  outputContext.fillStyle = "rgb(0,0,0)";
  outputContext.fillRect(0, 0, imageWidth, imageHeight);
  outputContext.imageSmoothingEnabled = true;
  outputContext.imageSmoothingQuality = "high";
  const views = [];
  try {
    for (const [index, view] of profile.views.entries()) {
      const reference = renderGeometry(renderer, target, referenceGeometry, view, profile, renderSize);
      const candidate = renderGeometry(renderer, target, candidateGeometry, view, profile, renderSize);
      const composed = composeTile(
        reference,
        candidate,
        view,
        profile,
        renderSize,
        exteriorDirections,
      );
      const column = index % profile.layout.columns;
      const row = Math.floor(index / profile.layout.columns);
      outputContext.drawImage(
        composed.canvas,
        column * tileWidth,
        row * tileHeight,
        tileWidth,
        tileHeight,
      );
      views.push({
        name: view.name,
        kind: view.kind,
        direction: view.direction,
        up: view.up,
        horizontal_flip: view.horizontal_flip,
        markers: composed.markers,
        framing: view.kind === "axial_depth"
          ? { projection: "orthographic", half_extent: profile.camera.orthographic.half_extent }
          : { projection: "perspective", vertical_fov_degrees: profile.camera.perspective.vertical_fov_degrees },
      });
    }
    return { ok: true, pngDataUrl: output.toDataURL("image/png"), views };
  } finally {
    referenceGeometry.dispose();
    candidateGeometry.dispose();
    target.dispose();
    renderer.dispose();
  }
}

window.__meshshotRender = async (payload) => {
  try {
    return await renderResidual(payload);
  } catch (error) {
    return { ok: false, error: String(error?.stack || error) };
  }
};
