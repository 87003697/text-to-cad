// Instance-batched rendering for very large component-GLB packages.
//
// Records keep their full identity — partId, baseTransform, effect/exploded
// matrices, and a real THREE.Mesh with BVH raycasting and faceIds — but those
// "logic" meshes live on a non-rendered layer. Pixels come from one
// THREE.InstancedMesh per unique component geometry: N occurrences collapse to
// ~M draw calls (M unique molds << N) in both the color and shadow passes.
//
// Per-part colors ride the per-instance color channel instead of materials, so
// same-mold bricks in different colors share one draw and every batch in a
// transparency class shares ONE material — consecutive draws are
// state-identical, which is what keeps per-draw overhead low on ANGLE/Metal
// (measured: per-draw fixed cost dominates this workload; multi-draw
// BatchedMesh is slower there because sub-draws are emulated per range).
//
// Trade-offs versus per-mesh rendering (mega-model territory only):
// - Hover/selection/dim tinting replaces the instance color (same hue the
//   per-mesh path paints on the material).
// - Per-record opacity boosts are not representable per instance; hidden and
//   effect-hidden records collapse their instance to a zero-scale matrix.
// - Per-record edge/silhouette overlays are skipped by the caller.

// Logic meshes render on no camera layer; raycasters opt in explicitly.
export const INSTANCED_RECORD_LOGIC_LAYER = 3;

const ZERO_MATRIX = Object.freeze([
  0, 0, 0, 0,
  0, 0, 0, 0,
  0, 0, 0, 0,
  0, 0, 0, 1
]);

function attributeSignature(geometry) {
  return Object.keys(geometry?.attributes || {}).sort().join(",");
}

// The GLB load path corner-expands every component (3 vertices per triangle),
// and mega-scene rendering is vertex-throughput bound. Welding bitwise-equal
// (position, normal, color) corners into an indexed geometry preserves the
// exact triangle order — faceIds, faceRuns, and BVH raycasts stay valid — while
// cutting vertex shader work ~2-3x. Surface-edge shader attributes are dropped:
// the batched mega path never renders edge overlays.
const WELD_MIN_VERTICES = 3_000;

function weldedRenderGeometry(THREE, geometry) {
  const cached = geometry.userData.__weldedRenderGeometry;
  if (cached) {
    return cached;
  }
  const position = geometry.attributes.position;
  if (!position || position.count < WELD_MIN_VERTICES) {
    return geometry;
  }
  const normal = geometry.attributes.normal;
  const color = geometry.attributes.color;
  const count = position.count;
  const pBits = new Uint32Array(position.array.buffer, position.array.byteOffset, count * 3);
  const nBits = normal ? new Uint32Array(normal.array.buffer, normal.array.byteOffset, count * 3) : null;
  const cBits = color ? new Uint32Array(color.array.buffer, color.array.byteOffset, count * 3) : null;
  const buckets = new Map();
  const remap = new Uint32Array(count);
  const keep = new Uint32Array(count);
  let welded = 0;
  // Axis-aligned CAD data is full of negative zeros, which are numerically
  // equal to +0 but bitwise distinct; canonicalize so they weld.
  const NEG_ZERO = 0x80000000;
  const canon = (bits) => (bits === NEG_ZERO ? 0 : bits);
  const equals = (a, b) => {
    const a3 = a * 3;
    const b3 = b * 3;
    if (
      canon(pBits[a3]) !== canon(pBits[b3]) ||
      canon(pBits[a3 + 1]) !== canon(pBits[b3 + 1]) ||
      canon(pBits[a3 + 2]) !== canon(pBits[b3 + 2])
    ) {
      return false;
    }
    if (nBits && (
      canon(nBits[a3]) !== canon(nBits[b3]) ||
      canon(nBits[a3 + 1]) !== canon(nBits[b3 + 1]) ||
      canon(nBits[a3 + 2]) !== canon(nBits[b3 + 2])
    )) {
      return false;
    }
    if (cBits && (
      canon(cBits[a3]) !== canon(cBits[b3]) ||
      canon(cBits[a3 + 1]) !== canon(cBits[b3 + 1]) ||
      canon(cBits[a3 + 2]) !== canon(cBits[b3 + 2])
    )) {
      return false;
    }
    return true;
  };
  for (let i = 0; i < count; i += 1) {
    const i3 = i * 3;
    let hash = (canon(pBits[i3]) ^ (canon(pBits[i3 + 1]) * 2654435761) ^ (canon(pBits[i3 + 2]) * 40503)) >>> 0;
    if (nBits) {
      hash = (hash ^ (canon(nBits[i3]) * 31) ^ canon(nBits[i3 + 1]) ^ (canon(nBits[i3 + 2]) * 7)) >>> 0;
    }
    let bucket = buckets.get(hash);
    if (bucket === undefined) {
      buckets.set(hash, i);
      remap[i] = welded;
      keep[welded] = i;
      welded += 1;
      continue;
    }
    if (typeof bucket === "number") {
      if (equals(bucket, i)) {
        remap[i] = remap[bucket];
        continue;
      }
      bucket = [bucket];
      buckets.set(hash, bucket);
    } else {
      let matched = -1;
      for (const candidate of bucket) {
        if (equals(candidate, i)) {
          matched = candidate;
          break;
        }
      }
      if (matched >= 0) {
        remap[i] = remap[matched];
        continue;
      }
    }
    bucket.push(i);
    remap[i] = welded;
    keep[welded] = i;
    welded += 1;
  }
  if (welded >= count * 0.85) {
    geometry.userData.__weldedRenderGeometry = geometry;
    return geometry;
  }
  const gather = (source, itemSize) => {
    const out = new Float32Array(welded * itemSize);
    for (let v = 0; v < welded; v += 1) {
      const src = keep[v] * itemSize;
      const dst = v * itemSize;
      for (let c = 0; c < itemSize; c += 1) {
        out[dst + c] = source[src + c];
      }
    }
    return out;
  };
  const weldedGeometry = new THREE.BufferGeometry();
  weldedGeometry.setAttribute("position", new THREE.BufferAttribute(gather(position.array, 3), 3));
  if (normal) {
    weldedGeometry.setAttribute("normal", new THREE.BufferAttribute(gather(normal.array, 3), 3));
  }
  if (color) {
    weldedGeometry.setAttribute("color", new THREE.BufferAttribute(gather(color.array, 3), 3));
  }
  // Preserve triangle order exactly: an existing index is remapped through the
  // weld, a soup geometry gets the weld remap directly.
  let indexArray = remap;
  if (geometry.index) {
    const sourceIndex = geometry.index.array;
    indexArray = new Uint32Array(sourceIndex.length);
    for (let i = 0; i < sourceIndex.length; i += 1) {
      indexArray[i] = remap[sourceIndex[i]];
    }
  }
  weldedGeometry.setIndex(new THREE.BufferAttribute(indexArray, 1));
  weldedGeometry.boundingSphere = geometry.boundingSphere ? geometry.boundingSphere.clone() : null;
  weldedGeometry.boundingBox = geometry.boundingBox ? geometry.boundingBox.clone() : null;
  weldedGeometry.userData.cadSceneCachedGeometry = geometry.userData.cadSceneCachedGeometry === true;
  geometry.userData.__weldedRenderGeometry = weldedGeometry;
  return weldedGeometry;
}

function bucketKeyForRecord(record) {
  const material = record?.material || {};
  return [
    record.geometry?.uuid || "geo",
    attributeSignature(record.geometry),
    material.wireframe ? "wire" : "solid",
    material.transparent ? `t:${Number(material.opacity ?? 1)}` : "opaque"
  ].join("|");
}

// Mirrors the record's object matrix (or a zero-scale collapse when the record
// is hidden) into its instance slot. Call after any write to record.mesh.matrix.
export function syncRecordInstanceTransform(record) {
  const slot = record?.instanceSlot;
  if (!slot?.batch) {
    return;
  }
  const visible = record.mesh?.visible !== false && slot.stateHidden !== true;
  if (!visible) {
    if (!slot.collapsed) {
      slot.batch.instanceMatrix.array.set(ZERO_MATRIX, slot.index * 16);
      slot.collapsed = true;
      slot.batch.instanceMatrix.needsUpdate = true;
    }
    return;
  }
  slot.collapsed = false;
  slot.batch.instanceMatrix.array.set(record.mesh.matrix.elements, slot.index * 16);
  slot.batch.instanceMatrix.needsUpdate = true;
}

function writeInstanceColor(slot, color) {
  const batch = slot.batch;
  const array = batch.instanceColor.array;
  const offset = slot.index * 3;
  if (array[offset] !== color.r || array[offset + 1] !== color.g || array[offset + 2] !== color.b) {
    array[offset] = color.r;
    array[offset + 1] = color.g;
    array[offset + 2] = color.b;
    batch.instanceColor.needsUpdate = true;
  }
}

// Applies hidden/tint state coming from applyPartVisualState. The tint
// replaces the instance's base color while active (matching the per-mesh
// path, which paints the highlight color onto the material).
export function syncRecordInstanceState(record, { hidden = false, tint = null } = {}) {
  const slot = record?.instanceSlot;
  if (!slot?.batch) {
    return;
  }
  slot.stateHidden = hidden === true;
  syncRecordInstanceTransform(record);
  if (tint) {
    writeInstanceColor(slot, tint);
    slot.tinted = true;
  } else if (slot.tinted) {
    writeInstanceColor(slot, slot.baseColor);
    slot.tinted = false;
  }
}

// Groups records over shared geometry into InstancedMesh batches (one per
// unique mold × transparency class), moves the logic meshes onto the
// non-rendered layer, and registers the batches in the scene.
export function buildInstancedRecordBatches(THREE, runtime, records) {
  const buckets = new Map();
  for (const record of Array.isArray(records) ? records : []) {
    if (!record?.mesh || !record.geometry) {
      continue;
    }
    const key = bucketKeyForRecord(record);
    let bucket = buckets.get(key);
    if (!bucket) {
      bucket = { records: [], geometry: record.geometry, material: record.material };
      buckets.set(key, bucket);
    }
    bucket.records.push(record);
  }

  // One shared material per (class signature): batches sort adjacently and
  // draw with zero state changes between them.
  const sharedMaterials = new Map();
  const white = new THREE.Color(1, 1, 1);
  const materialFor = (bucket) => {
    const source = bucket.material;
    const key = [
      source.type,
      attributeSignature(bucket.geometry),
      source.wireframe ? "wire" : "solid",
      source.transparent ? `t:${Number(source.opacity ?? 1)}` : "opaque"
    ].join("|");
    let material = sharedMaterials.get(key);
    if (!material) {
      material = source.clone();
      // Per-part color rides instanceColor; the material stays white so the
      // multiply leaves the instance color exact. Vertex-colored geometry
      // keeps its baked colors (instance color defaults to white identity).
      if (material.color) {
        material.color.set(1, 1, 1);
      }
      material.vertexColors = /(^|,)color(,|$)/.test(attributeSignature(bucket.geometry));
      sharedMaterials.set(key, material);
    }
    return material;
  };

  const batches = [];
  for (const bucket of buckets.values()) {
    const batch = new THREE.InstancedMesh(weldedRenderGeometry(THREE, bucket.geometry), materialFor(bucket), bucket.records.length);
    batch.castShadow = true;
    batch.receiveShadow = false;
    // Instances span the whole model; whole-batch culling would pop groups of
    // parts in and out at the viewport edge.
    batch.frustumCulled = false;
    // Picking runs against the BVH-backed logic meshes.
    batch.raycast = () => {};
    batch.userData.instancedRecordBatch = true;
    batch.userData.beforeDispose = () => batch.dispose();
    batch.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(bucket.records.length * 3).fill(1), 3);
    bucket.records.forEach((record, index) => {
      const slot = {
        batch,
        index,
        collapsed: false,
        stateHidden: false,
        tinted: false,
        baseColor: !record.useVertexColors && record.baseColor && record.baseColor.isColor
          ? record.baseColor.clone()
          : white.clone()
      };
      record.instanceSlot = slot;
      // Vertex-colored geometry keeps a white instance color (multiply
      // identity); flat-colored parts carry their color per instance.
      if (!record.useVertexColors) {
        writeInstanceColor(slot, slot.baseColor);
      }
      record.mesh.layers.set(INSTANCED_RECORD_LOGIC_LAYER);
      if (record.mesh.castShadow) {
        record.mesh.castShadow = false;
      }
      syncRecordInstanceTransform(record);
    });
    runtime.modelGroup.add(batch);
    batches.push(batch);
  }
  return batches;
}

// Raycasters test object layers; give the app raycaster access to the logic
// meshes once instanced batches are active.
export function enableInstancedRecordRaycast(raycaster) {
  raycaster?.layers?.enable?.(INSTANCED_RECORD_LOGIC_LAYER);
}
