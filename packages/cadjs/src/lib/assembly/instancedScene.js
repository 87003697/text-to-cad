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
  geometry.computeBoundingBox();
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
    // Component-local AABB, shared by every instance of this cid. The per-occurrence
    // world bounds (used by zoom-to-fit-selection) transform this box by an instance's
    // base matrix — see instancedOccurrenceBounds.
    const box = geometry.boundingBox;
    mesh.userData.cadInstanceComponentBox = box
      ? { min: [box.min.x, box.min.y, box.min.z], max: [box.max.x, box.max.y, box.max.z] }
      : null;
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

// Per-instance overlay codes (selection/hover/focus-dim/hidden) folded into the
// per-instance colour + matrix signatures below.
const STATE_BASE = 0;
const STATE_SELECTED = 1;
const STATE_HOVERED = 2;
const STATE_DIMMED = 3;
const STATE_HIDDEN = 4;

// Version fields are masked so the packed 32-bit signatures never overflow. A wrap
// (after ~500k transform syncs) can at worst skip a single frame's write for an
// instance whose pose is unchanged anyway; it self-corrects on the next sync.
const POSE_VERSION_MASK = 0x7ffff;
const STYLE_VERSION_MASK = 0xffff;

function defaultMatches(partId, set) {
  return set instanceof Set && set.has(partId);
}

function resolveEffectColorTriplet(THREE, color) {
  if (color == null) {
    return null;
  }
  if (Array.isArray(color) && color.length >= 3) {
    return [Number(color[0]) || 0, Number(color[1]) || 0, Number(color[2]) || 0];
  }
  try {
    const c = new THREE.Color(color);
    return [c.r, c.g, c.b];
  } catch {
    return null;
  }
}

// World-space AABB of a single occurrence: transform the shared component-local box by
// this instance's base matrix (stored column-major in three.js Matrix4.elements order).
function occurrenceWorldBounds(box, baseMatrices, index) {
  if (!box || !baseMatrices) {
    return null;
  }
  const [nx, ny, nz] = box.min;
  const [xx, xy, xz] = box.max;
  const corners = [
    [nx, ny, nz], [xx, ny, nz], [nx, xy, nz], [xx, xy, nz],
    [nx, ny, xz], [xx, ny, xz], [nx, xy, xz], [xx, xy, xz]
  ];
  const o = index * 16;
  const e0 = baseMatrices[o], e1 = baseMatrices[o + 1], e2 = baseMatrices[o + 2];
  const e4 = baseMatrices[o + 4], e5 = baseMatrices[o + 5], e6 = baseMatrices[o + 6];
  const e8 = baseMatrices[o + 8], e9 = baseMatrices[o + 9], e10 = baseMatrices[o + 10];
  const e12 = baseMatrices[o + 12], e13 = baseMatrices[o + 13], e14 = baseMatrices[o + 14];
  const min = [Infinity, Infinity, Infinity];
  const max = [-Infinity, -Infinity, -Infinity];
  for (const [x, y, z] of corners) {
    const wx = e0 * x + e4 * y + e8 * z + e12;
    const wy = e1 * x + e5 * y + e9 * z + e13;
    const wz = e2 * x + e6 * y + e10 * z + e14;
    if (wx < min[0]) min[0] = wx; if (wx > max[0]) max[0] = wx;
    if (wy < min[1]) min[1] = wy; if (wy > max[1]) max[1] = wy;
    if (wz < min[2]) min[2] = wz; if (wz > max[2]) max[2] = wz;
  }
  return { min, max };
}

/**
 * Build one lightweight per-occurrence record per instance across all buckets.
 *
 * These carry the per-occurrence identity (partId = occurrence id), world bounds, and a
 * link back to `{instanceMesh, instanceIndex}` — but NO geometry/material/THREE.Mesh, so
 * they are cheap even at 2,000+ occurrences and preserve the instancing GPU win. They
 * satisfy the same shape the per-mesh record pipelines consume (recordCanExplode wants a
 * truthy `mesh` + non-null partId; the effect applier keys on partId), so the shared
 * exploded-view and step-module effect engines write `explodedViewMatrix` / `effectMatrix`
 * onto them unchanged. `syncInstancedOccurrenceTransforms` then flushes those offsets into
 * the InstancedMesh instance buffers.
 */
export function buildInstancedOccurrenceRecords(THREE, instancedMeshes) {
  const records = [];
  for (const mesh of Array.isArray(instancedMeshes) ? instancedMeshes : []) {
    const ud = mesh?.userData || {};
    const ids = ud.cadInstanceOccurrenceIds;
    const baseMatrices = ud.cadInstanceBaseMatrices;
    if (!Array.isArray(ids) || !baseMatrices) {
      continue;
    }
    const box = ud.cadInstanceComponentBox;
    for (let index = 0; index < ids.length; index += 1) {
      records.push({
        partId: ids[index],
        partBounds: occurrenceWorldBounds(box, baseMatrices, index),
        // `mesh` only flags the record as live for recordCanExplode; the flush writes an
        // instance row via instanceMesh/instanceIndex, never mesh.matrix.
        mesh,
        instanced: true,
        instanceMesh: mesh,
        instanceIndex: index,
        baseMatrix: new THREE.Matrix4().fromArray(baseMatrices, index * 16),
        explodedViewMatrix: null,
        effectMatrix: null,
        effectStyle: null,
        effectVisible: null,
        effectHighlighted: false
      });
    }
  }
  return records;
}

/**
 * Flush per-occurrence transform/effect state (written by the exploded-view engine and the
 * step-module effect applier onto the pseudo-records) into per-mesh instance-buffer slices,
 * then re-sync each touched bucket. The posed matrix matches the per-mesh composition
 * order in composeDisplayRecordObjectMatrix: explodedViewMatrix · effectMatrix · baseMatrix.
 * GPU writes happen in syncInstancedMesh so a single writer owns instanceMatrix/instanceColor.
 */
export function syncInstancedOccurrenceTransforms(THREE, records) {
  if (!THREE?.Matrix4) {
    return;
  }
  const byMesh = new Map();
  for (const record of Array.isArray(records) ? records : []) {
    const mesh = record?.instanceMesh;
    if (!mesh) {
      continue;
    }
    let list = byMesh.get(mesh);
    if (!list) {
      list = [];
      byMesh.set(mesh, list);
    }
    list.push(record);
  }
  const tmp = new THREE.Matrix4();
  for (const [mesh, list] of byMesh) {
    const ud = mesh.userData || (mesh.userData = {});
    const baseMatrices = ud.cadInstanceBaseMatrices;
    const count = Array.isArray(ud.cadInstanceOccurrenceIds) ? ud.cadInstanceOccurrenceIds.length : 0;
    if (!baseMatrices || !count) {
      continue;
    }
    let posed = ud.cadInstancePosedMatrices;
    if (!(posed instanceof Float32Array) || posed.length !== count * 16) {
      posed = new Float32Array(count * 16);
      ud.cadInstancePosedMatrices = posed;
    }
    let effColors = ud.cadInstanceEffectColors;
    if (!(effColors instanceof Float32Array) || effColors.length !== count * 3) {
      effColors = new Float32Array(count * 3);
      ud.cadInstanceEffectColors = effColors;
    }
    let effMask = ud.cadInstanceEffectColorMask;
    if (!(effMask instanceof Uint8Array) || effMask.length !== count) {
      effMask = new Uint8Array(count);
      ud.cadInstanceEffectColorMask = effMask;
    }
    let effHidden = ud.cadInstanceEffectHidden;
    if (!(effHidden instanceof Uint8Array) || effHidden.length !== count) {
      effHidden = new Uint8Array(count);
      ud.cadInstanceEffectHidden = effHidden;
    }
    let effHi = ud.cadInstanceEffectHighlighted;
    if (!(effHi instanceof Uint8Array) || effHi.length !== count) {
      effHi = new Uint8Array(count);
      ud.cadInstanceEffectHighlighted = effHi;
    }
    for (const record of list) {
      const i = record.instanceIndex;
      if (!Number.isInteger(i) || i < 0 || i >= count) {
        continue;
      }
      tmp.fromArray(baseMatrices, i * 16);
      const effect = record.effectMatrix instanceof THREE.Matrix4 ? record.effectMatrix : null;
      const explode = record.explodedViewMatrix instanceof THREE.Matrix4 ? record.explodedViewMatrix : null;
      if (effect) {
        tmp.premultiply(effect);
      }
      if (explode) {
        tmp.premultiply(explode);
      }
      posed.set(tmp.elements, i * 16);
      effHidden[i] = record.effectVisible === false ? 1 : 0;
      effHi[i] = record.effectHighlighted === true ? 1 : 0;
      const triplet = resolveEffectColorTriplet(THREE, record.effectStyle?.color);
      if (triplet) {
        effMask[i] = 1;
        effColors[i * 3] = triplet[0];
        effColors[i * 3 + 1] = triplet[1];
        effColors[i * 3 + 2] = triplet[2];
      } else {
        effMask[i] = 0;
      }
    }
    ud.cadInstancePoseVersion = (((ud.cadInstancePoseVersion | 0) + 1) & POSE_VERSION_MASK);
    ud.cadInstanceStyleVersion = (((ud.cadInstanceStyleVersion | 0) + 1) & STYLE_VERSION_MASK);
    syncInstancedMesh(THREE, mesh);
  }
}

/**
 * The single writer of an instanced bucket's instanceMatrix/instanceColor buffers. Combines
 * the visual-state slice (selection/hover/hidden/focus — stored by applyInstancedVisualState)
 * with the transform/effect slices (posed matrices, effect colours/visibility/highlight —
 * stored by syncInstancedOccurrenceTransforms):
 *   - matrix  = hidden ? collapsed : posed-or-base
 *   - colour  = overlay(selection/hover/dim) over (effect colour or base colour)
 * Per-instance packed signatures skip unchanged instances so hover on a static package still
 * touches only the ~2 instances that changed (no full re-upload); a pose/style version bump
 * (explode / param animation) rewrites the affected instances. Pure + THREE-injected.
 *
 * @returns true when any instance attribute changed (tests assert on it).
 */
export function syncInstancedMesh(THREE, mesh) {
  const ud = mesh?.userData;
  const occurrenceIds = ud?.cadInstanceOccurrenceIds;
  const baseColors = ud?.cadInstanceBaseColors;
  const baseMatrices = ud?.cadInstanceBaseMatrices;
  if (!Array.isArray(occurrenceIds) || !occurrenceIds.length || !baseColors || !baseMatrices) {
    return false;
  }
  const count = occurrenceIds.length;

  const vs = ud.cadVisualState || {};
  const matches = typeof vs.matches === "function" ? vs.matches : defaultMatches;
  const hasFocus = vs.hasFocus === true || (vs.focusIds instanceof Set && vs.focusIds.size > 0);
  const dimFactor = Number.isFinite(vs.dimFactor) ? vs.dimFactor : DIMMED_INSTANCE_FACTOR;
  const selR = vs.selectedColor?.r ?? 0.31;
  const selG = vs.selectedColor?.g ?? 0.615;
  const selB = vs.selectedColor?.b ?? 1;
  const hovR = vs.hoveredColor?.r ?? 0.553;
  const hovG = vs.hoveredColor?.g ?? 0.772;
  const hovB = vs.hoveredColor?.b ?? 1;

  const posed = ud.cadInstancePosedMatrices instanceof Float32Array ? ud.cadInstancePosedMatrices : null;
  const effColors = ud.cadInstanceEffectColors instanceof Float32Array ? ud.cadInstanceEffectColors : null;
  const effMask = ud.cadInstanceEffectColorMask instanceof Uint8Array ? ud.cadInstanceEffectColorMask : null;
  const effHidden = ud.cadInstanceEffectHidden instanceof Uint8Array ? ud.cadInstanceEffectHidden : null;
  const effHi = ud.cadInstanceEffectHighlighted instanceof Uint8Array ? ud.cadInstanceEffectHighlighted : null;
  const poseVersion = (ud.cadInstancePoseVersion | 0) & POSE_VERSION_MASK;
  const styleVersion = (ud.cadInstanceStyleVersion | 0) & STYLE_VERSION_MASK;

  let matrixSig = ud.cadInstanceMatrixSig;
  if (!(matrixSig instanceof Uint32Array) || matrixSig.length !== count) {
    matrixSig = new Uint32Array(count).fill(0xffffffff);
    ud.cadInstanceMatrixSig = matrixSig;
  }
  let colorSig = ud.cadInstanceColorSig;
  if (!(colorSig instanceof Uint32Array) || colorSig.length !== count) {
    colorSig = new Uint32Array(count).fill(0xffffffff);
    ud.cadInstanceColorSig = colorSig;
  }

  const tmpColor = new THREE.Color();
  const tmpMatrix = new THREE.Matrix4();
  let matrixChanged = false;
  let colorChanged = false;

  for (let index = 0; index < count; index += 1) {
    const id = occurrenceIds[index];
    const isHidden = (effHidden ? effHidden[index] === 1 : false) || matches(id, vs.hidden);
    const isSelected = !isHidden && ((effHi ? effHi[index] === 1 : false) || matches(id, vs.selected));
    const isHovered = !isHidden && !isSelected && matches(id, vs.hovered);
    const isFocused = !isHidden && hasFocus && matches(id, vs.focusIds);
    const isDimmed = !isHidden && hasFocus && !isFocused && !isSelected && !isHovered;
    const code = isHidden ? STATE_HIDDEN
      : isSelected ? STATE_SELECTED
        : isHovered ? STATE_HOVERED
          : isDimmed ? STATE_DIMMED
            : STATE_BASE;

    // Matrix: collapse when hidden, else the posed transform (or base when no offsets).
    // The pose version forces a rewrite when explode/animation moved the instances.
    const nextMatrixSig = (isHidden ? 1 : 0) | (poseVersion << 1);
    if (matrixSig[index] !== nextMatrixSig) {
      if (isHidden) {
        tmpMatrix.fromArray(HIDDEN_INSTANCE_MATRIX);
      } else if (posed) {
        tmpMatrix.fromArray(posed, index * 16);
      } else {
        tmpMatrix.fromArray(baseMatrices, index * 16);
      }
      mesh.setMatrixAt(index, tmpMatrix);
      matrixSig[index] = nextMatrixSig;
      matrixChanged = true;
    }

    // Colour: selection/hover/dim overlay over the effect colour (if any) or base colour.
    const nextColorSig = code | (styleVersion << 3);
    if (colorSig[index] !== nextColorSig) {
      const hasEffectColor = effMask ? effMask[index] === 1 : false;
      let r = hasEffectColor ? effColors[index * 3] : baseColors[index * 3];
      let g = hasEffectColor ? effColors[index * 3 + 1] : baseColors[index * 3 + 1];
      let b = hasEffectColor ? effColors[index * 3 + 2] : baseColors[index * 3 + 2];
      if (code === STATE_SELECTED) {
        r = selR; g = selG; b = selB;
      } else if (code === STATE_HOVERED) {
        r = hovR; g = hovG; b = hovB;
      } else if (code === STATE_DIMMED) {
        r *= dimFactor; g *= dimFactor; b *= dimFactor;
      }
      tmpColor.setRGB(r, g, b);
      mesh.setColorAt(index, tmpColor);
      colorSig[index] = nextColorSig;
      colorChanged = true;
    }
  }

  if (matrixChanged) {
    mesh.instanceMatrix.needsUpdate = true;
  }
  if (colorChanged && mesh.instanceColor) {
    mesh.instanceColor.needsUpdate = true;
  }
  return matrixChanged || colorChanged;
}

/**
 * Store the selection/hover/hidden/focus slice for a bucket and re-sync it. The actual
 * per-instance buffer writes happen in syncInstancedMesh, which merges this slice with any
 * transform/effect slice so selection and exploded/animated poses coexist without racing on
 * the shared instanceMatrix/instanceColor buffers.
 *
 * Sets are matched with `matches` (cadScene passes its hierarchical partIdMatchesSet; the
 * default is exact Set membership). Pure + THREE-injected for Node testability.
 *
 * @returns true when any instance attribute changed.
 */
export function applyInstancedVisualState(THREE, mesh, state = {}) {
  if (!mesh?.userData) {
    return false;
  }
  mesh.userData.cadVisualState = {
    ...state,
    hasFocus: state.hasFocus ?? (state.focusIds instanceof Set && state.focusIds.size > 0),
    matches: typeof state.matches === "function" ? state.matches : defaultMatches
  };
  return syncInstancedMesh(THREE, mesh);
}

// World-space AABB of the occurrences in one instanced bucket that satisfy `matches`
// (an (occurrenceId) => boolean predicate). Transforms the shared component-local box
// by each selected instance's base matrix and unions the results. Pure JS (no THREE):
// base matrices are stored column-major (three.js Matrix4.elements), so for a point
// p, world = e[0]*x+e[4]*y+e[8]*z+e[12], etc. Returns { min:[3], max:[3] } or null.
// This is what lets zoom-to-fit frame a selected occurrence that has no per-record
// partBounds (instanced records are one-per-bucket, ids live per instance).
export function instancedOccurrenceBounds(mesh, matches) {
  const occurrenceIds = mesh?.userData?.cadInstanceOccurrenceIds;
  const baseMatrices = mesh?.userData?.cadInstanceBaseMatrices;
  const box = mesh?.userData?.cadInstanceComponentBox;
  if (!Array.isArray(occurrenceIds) || !baseMatrices || !box) {
    return null;
  }
  const predicate = typeof matches === "function" ? matches : () => true;
  const [nx, ny, nz] = box.min;
  const [xx, xy, xz] = box.max;
  const corners = [
    [nx, ny, nz], [xx, ny, nz], [nx, xy, nz], [xx, xy, nz],
    [nx, ny, xz], [xx, ny, xz], [nx, xy, xz], [xx, xy, xz]
  ];
  const min = [Infinity, Infinity, Infinity];
  const max = [-Infinity, -Infinity, -Infinity];
  let matched = 0;
  for (let index = 0; index < occurrenceIds.length; index += 1) {
    if (!predicate(occurrenceIds[index])) {
      continue;
    }
    const o = index * 16;
    const e0 = baseMatrices[o], e1 = baseMatrices[o + 1], e2 = baseMatrices[o + 2];
    const e4 = baseMatrices[o + 4], e5 = baseMatrices[o + 5], e6 = baseMatrices[o + 6];
    const e8 = baseMatrices[o + 8], e9 = baseMatrices[o + 9], e10 = baseMatrices[o + 10];
    const e12 = baseMatrices[o + 12], e13 = baseMatrices[o + 13], e14 = baseMatrices[o + 14];
    for (const [x, y, z] of corners) {
      const wx = e0 * x + e4 * y + e8 * z + e12;
      const wy = e1 * x + e5 * y + e9 * z + e13;
      const wz = e2 * x + e6 * y + e10 * z + e14;
      if (wx < min[0]) min[0] = wx; if (wx > max[0]) max[0] = wx;
      if (wy < min[1]) min[1] = wy; if (wy > max[1]) max[1] = wy;
      if (wz < min[2]) min[2] = wz; if (wz > max[2]) max[2] = wz;
    }
    matched += 1;
  }
  return matched > 0 ? { min, max } : null;
}
