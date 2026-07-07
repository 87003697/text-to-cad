// cid-keyed instanced rendering for component-GLB packages.
//
// A package descriptor groups N occurrences over M unique components (M << N:
// falcon_heavy is 2,142 occurrences / 141 components). buildComposedPackageMeshData
// bakes every occurrence's transform into fresh per-occurrence vertices — M unique
// component vertex sets inflate to N placed sets (~12× for falcon_heavy) and become
// N THREE.Mesh draw calls. This module renders the SAME scene as one InstancedMesh
// per (component, material-bucket): the component geometry is uploaded ONCE and each
// occurrence contributes only a 4×4 instance matrix (+ optional instance color), so
// GPU vertices collapse to the unique set and draw calls collapse to ~M.
//
// Framework-agnostic (THREE is injected, like cadScene). This is the geometry/
// transform engine; wiring it into cadScene's record system (picking, selection,
// exploded view, edges) is layered on top separately.

const IDENTITY_MATRIX = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];

function toMatrixArray(value) {
  if (Array.isArray(value) && value.length === 16) {
    return value.map((n) => Number(n) || 0);
  }
  return IDENTITY_MATRIX.slice();
}

// Determinant of the upper-left 3×3 of a row-major 4×4. Negative => a mirrored
// (reflection) transform, which flips triangle winding per instance; three.js
// does not re-derive winding per instance, so mirrored occurrences render in a
// separate DoubleSide bucket to stay lit/visible from both faces.
function matrixDeterminant3(m) {
  const a = m[0], b = m[1], c = m[2];
  const d = m[4], e = m[5], f = m[6];
  const g = m[8], h = m[9], i = m[10];
  return a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g);
}

function toColorTriplet(value) {
  if (Array.isArray(value) && value.length >= 3) {
    return [Number(value[0]) || 0, Number(value[1]) || 0, Number(value[2]) || 0];
  }
  return null;
}

function componentGeometry(THREE, componentMeshData) {
  const geometry = new THREE.BufferGeometry();
  const vertices = componentMeshData?.vertices || new Float32Array(0);
  const normals = componentMeshData?.normals || new Float32Array(0);
  const colors = componentMeshData?.colors || new Float32Array(0);
  const indices = componentMeshData?.indices || new Uint32Array(0);
  geometry.setAttribute("position", new THREE.BufferAttribute(vertices, 3));
  if (normals.length === vertices.length && normals.length > 0) {
    geometry.setAttribute("normal", new THREE.BufferAttribute(normals, 3));
  }
  if (colors.length === vertices.length && colors.length > 0) {
    geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
  }
  if (indices.length > 0) {
    geometry.setIndex(new THREE.BufferAttribute(indices, 1));
  }
  geometry.computeBoundingSphere();
  return geometry;
}

/**
 * Build the instanced scene graph for a component-GLB package.
 *
 * @param THREE  the three.js module (injected)
 * @param descriptor  the package descriptor (occurrences + components)
 * @param componentMeshDataByCid  { cid -> parsed component meshData }
 * @param opts.makeMaterial  (cid, { hasVertexColors, doubleSide }) -> Material.
 *        Defaults to a MeshStandardMaterial (vertexColors when the geometry has
 *        a color attribute). Callers pass cadScene's material factory to match
 *        the per-mesh path's appearance.
 * @returns { group, instancedMeshes, drawCalls, gpuVertices, occurrenceCount, componentCount }
 */
export function buildInstancedPackageScene(THREE, descriptor, componentMeshDataByCid, opts = {}) {
  if (!THREE) {
    throw new Error("buildInstancedPackageScene requires THREE");
  }
  const occurrences = Array.isArray(descriptor?.occurrences) ? descriptor.occurrences : [];
  if (!occurrences.length) {
    throw new Error("Assembly package descriptor has no occurrences");
  }
  const makeMaterial = typeof opts.makeMaterial === "function"
    ? opts.makeMaterial
    : (_cid, { hasVertexColors, doubleSide }) => new THREE.MeshStandardMaterial({
        vertexColors: !!hasVertexColors,
        side: doubleSide ? THREE.DoubleSide : THREE.FrontSide,
        metalness: 0.1,
        roughness: 0.6
      });

  // Bucket occurrences by (cid, mirrored). Each bucket is one InstancedMesh.
  const buckets = new Map(); // key -> { cid, mirrored, occurrences: [] }
  for (const occurrence of occurrences) {
    const cid = String(occurrence?.component || "").trim();
    const componentMeshData = componentMeshDataByCid?.[cid];
    if (!cid || !componentMeshData || !(componentMeshData.vertices?.length > 0)) {
      continue;
    }
    const matrix = toMatrixArray(occurrence?.transform);
    const mirrored = matrixDeterminant3(matrix) < 0;
    const key = `${cid}:${mirrored ? "m" : "n"}`;
    let bucket = buckets.get(key);
    if (!bucket) {
      bucket = { cid, mirrored, occurrences: [] };
      buckets.set(key, bucket);
    }
    bucket.occurrences.push({ occurrence, matrix });
  }
  if (!buckets.size) {
    throw new Error("Assembly package matched no renderable component GLBs");
  }

  const group = new THREE.Group();
  group.name = "CadInstancedModel";
  const geometryByCid = new Map();
  const instancedMeshes = [];
  let gpuVertices = 0;

  const tmpMatrix = new THREE.Matrix4();
  const tmpColor = THREE.Color ? new THREE.Color() : null;

  for (const bucket of buckets.values()) {
    const componentMeshData = componentMeshDataByCid[bucket.cid];
    let geometry = geometryByCid.get(bucket.cid);
    if (!geometry) {
      geometry = componentGeometry(THREE, componentMeshData);
      geometryByCid.set(bucket.cid, geometry);
      gpuVertices += (componentMeshData.vertices?.length || 0) / 3;
    }
    const hasVertexColors = !!geometry.getAttribute("color");
    const anyOverrideColor = bucket.occurrences.some(
      ({ occurrence }) => toColorTriplet(occurrence?.color) !== null
    );
    const material = makeMaterial(bucket.cid, {
      hasVertexColors: hasVertexColors && !anyOverrideColor,
      doubleSide: bucket.mirrored
    });

    const count = bucket.occurrences.length;
    const mesh = new THREE.InstancedMesh(geometry, material, count);
    mesh.name = `CadInstanced:${bucket.cid}${bucket.mirrored ? ":mirror" : ""}`;
    mesh.frustumCulled = true;
    // Map each instance index back to its descriptor occurrence for picking/selection.
    const instanceOccurrenceIds = new Array(count);
    // Base per-instance state kept so the visual-state layer (selection/hover/hide/
    // exploded) can recolor or move a single instance and restore it afterward.
    // instanceColor is always allocated (base = override color, else white) so a
    // later setColorAt on one instance never leaves the rest at the zero-init black
    // three.js would give a lazily-allocated instanceColor buffer.
    const baseColors = new Float32Array(count * 3);
    const baseMatrices = new Float32Array(count * 16);

    for (let index = 0; index < count; index += 1) {
      const { occurrence, matrix } = bucket.occurrences[index];
      // three.js Matrix4.set is row-major; the descriptor transform is row-major.
      tmpMatrix.set(
        matrix[0], matrix[1], matrix[2], matrix[3],
        matrix[4], matrix[5], matrix[6], matrix[7],
        matrix[8], matrix[9], matrix[10], matrix[11],
        matrix[12], matrix[13], matrix[14], matrix[15]
      );
      mesh.setMatrixAt(index, tmpMatrix);
      baseMatrices.set(tmpMatrix.elements, index * 16);
      instanceOccurrenceIds[index] = String(occurrence?.id || "").trim();
      const override = toColorTriplet(occurrence?.color);
      const r = override ? override[0] : 1;
      const g = override ? override[1] : 1;
      const b = override ? override[2] : 1;
      baseColors[index * 3] = r;
      baseColors[index * 3 + 1] = g;
      baseColors[index * 3 + 2] = b;
      if (tmpColor && mesh.setColorAt) {
        tmpColor.setRGB(r, g, b);
        mesh.setColorAt(index, tmpColor);
      }
    }
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) {
      mesh.instanceColor.needsUpdate = true;
    }
    mesh.userData.cadInstanceOccurrenceIds = instanceOccurrenceIds;
    mesh.userData.cadInstanceBaseColors = baseColors;
    mesh.userData.cadInstanceBaseMatrices = baseMatrices;
    mesh.userData.cadComponentId = bucket.cid;
    group.add(mesh);
    instancedMeshes.push(mesh);
  }

  return {
    group,
    instancedMeshes,
    drawCalls: instancedMeshes.length,
    gpuVertices,
    occurrenceCount: occurrences.length,
    componentCount: geometryByCid.size
  };
}

const HIDDEN_INSTANCE_MATRIX = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0];
const DIMMED_INSTANCE_FACTOR = 0.28;

function defaultMatches(partId, set) {
  return set instanceof Set && set.has(partId);
}

/**
 * Apply per-occurrence selection/hover/hidden/focus state to one instanced bucket.
 *
 * A shared bucket material cannot express per-instance emissive glow or opacity,
 * so selection/hover read out as an instanceColor recolor, focus-dimming as a
 * darkened instanceColor, and hide as a collapsed (zero) instance matrix. Base
 * color and base matrix are restored from the arrays stashed at build time, so
 * toggling state back returns the instance to its authored appearance/pose.
 *
 * Pure and THREE-injected for Node testability. Sets are matched with `matches`
 * (cadScene passes its hierarchical partIdMatchesSet; the default is exact Set
 * membership).
 *
 * @returns true when any instance attribute changed (caller need not track it —
 *          needsUpdate is set here — but tests assert on it).
 */
export function applyInstancedVisualState(THREE, mesh, state = {}) {
  const occurrenceIds = mesh?.userData?.cadInstanceOccurrenceIds;
  const baseColors = mesh?.userData?.cadInstanceBaseColors;
  const baseMatrices = mesh?.userData?.cadInstanceBaseMatrices;
  if (!Array.isArray(occurrenceIds) || !occurrenceIds.length || !baseColors || !baseMatrices) {
    return false;
  }
  const {
    selected,
    hovered,
    hidden,
    focusIds,
    hasFocus = focusIds instanceof Set && focusIds.size > 0,
    selectedColor,
    hoveredColor,
    dimFactor = DIMMED_INSTANCE_FACTOR,
    matches = defaultMatches
  } = state;

  const selR = selectedColor?.r ?? 0.31;
  const selG = selectedColor?.g ?? 0.615;
  const selB = selectedColor?.b ?? 1;
  const hovR = hoveredColor?.r ?? 0.553;
  const hovG = hoveredColor?.g ?? 0.772;
  const hovB = hoveredColor?.b ?? 1;

  const tmpColor = new THREE.Color();
  const tmpMatrix = new THREE.Matrix4();
  let colorChanged = false;
  let matrixChanged = false;

  for (let index = 0; index < occurrenceIds.length; index += 1) {
    const id = occurrenceIds[index];
    const isHidden = matches(id, hidden);
    const isSelected = !isHidden && matches(id, selected);
    const isHovered = !isHidden && !isSelected && matches(id, hovered);
    const isFocused = !isHidden && hasFocus && matches(id, focusIds);
    const isDimmed = !isHidden && hasFocus && !isFocused && !isSelected && !isHovered;

    // Matrix: hidden collapses to a zero instance; everything else rides its base pose.
    if (isHidden) {
      tmpMatrix.fromArray(HIDDEN_INSTANCE_MATRIX);
    } else {
      tmpMatrix.fromArray(baseMatrices, index * 16);
    }
    mesh.setMatrixAt(index, tmpMatrix);
    matrixChanged = true;

    // Color: selection/hover recolor, focus-dim darkens the base, else the base color.
    let r = baseColors[index * 3];
    let g = baseColors[index * 3 + 1];
    let b = baseColors[index * 3 + 2];
    if (isSelected) {
      r = selR; g = selG; b = selB;
    } else if (isHovered) {
      r = hovR; g = hovG; b = hovB;
    } else if (isDimmed) {
      r *= dimFactor; g *= dimFactor; b *= dimFactor;
    }
    tmpColor.setRGB(r, g, b);
    mesh.setColorAt(index, tmpColor);
    colorChanged = true;
  }

  if (matrixChanged) {
    mesh.instanceMatrix.needsUpdate = true;
  }
  if (colorChanged && mesh.instanceColor) {
    mesh.instanceColor.needsUpdate = true;
  }
  return colorChanged || matrixChanged;
}
