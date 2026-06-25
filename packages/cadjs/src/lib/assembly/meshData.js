import { mergeBounds } from "../urdf/kinematics.js";

const IDENTITY_TRANSFORM = Object.freeze([
  1, 0, 0, 0,
  0, 1, 0, 0,
  0, 0, 1, 0,
  0, 0, 0, 1
]);

function toTransformArray(value) {
  if (!Array.isArray(value) || value.length !== 16) {
    return [...IDENTITY_TRANSFORM];
  }
  return value.map((component, index) => Number.isFinite(Number(component)) ? Number(component) : IDENTITY_TRANSFORM[index]);
}

export function assemblyRootFromTopology(topologyManifest) {
  const root = topologyManifest?.assembly?.root;
  return root && typeof root === "object" ? root : null;
}

function toVectorArray(value) {
  if (!Array.isArray(value) || value.length < 3) {
    return null;
  }
  const vector = value.slice(0, 3).map((component) => Number(component));
  return vector.every((component) => Number.isFinite(component)) ? vector : null;
}

function normalizeMateEndpoint(endpoint) {
  if (!endpoint || typeof endpoint !== "object") {
    return null;
  }
  const result = {
    part: String(endpoint.part || "").trim(),
    frame: String(endpoint.frame || "").trim()
  };
  const position = toVectorArray(endpoint.position);
  const orientation = toVectorArray(endpoint.orientation);
  if (position) {
    result.position = position;
  }
  if (orientation) {
    result.orientation = orientation;
  }
  const axes = endpoint.axes && typeof endpoint.axes === "object" ? endpoint.axes : null;
  if (axes) {
    const normalizedAxes = {};
    for (const key of ["x", "y", "z"]) {
      const axis = toVectorArray(axes[key]);
      if (axis) {
        normalizedAxes[key] = axis;
      }
    }
    if (Object.keys(normalizedAxes).length) {
      result.axes = normalizedAxes;
    }
  }
  return result.position || result.orientation || result.part || result.frame ? result : null;
}

export function assemblyMatesFromTopology(topologyManifest) {
  const mates = topologyManifest?.assemblyMates;
  if (!Array.isArray(mates)) {
    return [];
  }
  return mates
    .filter((mate) => mate && typeof mate === "object")
    .map((mate, index) => {
      const id = String(mate.id || `m${index + 1}`).trim() || `m${index + 1}`;
      return {
        id,
        label: String(mate.label || id).trim() || id,
        sourceLabel: String(mate.sourceLabel || mate.name || "").trim(),
        type: String(mate.type || mate.relation || "mate").trim(),
        relation: String(mate.relation || mate.type || "mate").trim(),
        fixed: String(mate.fixed || "").trim(),
        moving: String(mate.moving || "").trim(),
        parameters: mate.parameters && typeof mate.parameters === "object" ? mate.parameters : {},
        fixedEndpoint: normalizeMateEndpoint(mate.fixedEndpoint),
        movingEndpoint: normalizeMateEndpoint(mate.movingEndpoint)
      };
    });
}

export function flattenAssemblyLeafParts(root) {
  const leafParts = [];
  const stack = root ? [root] : [];
  while (stack.length) {
    const node = stack.pop();
    const children = Array.isArray(node?.children) ? node.children : [];
    if (children.length) {
      for (let index = children.length - 1; index >= 0; index -= 1) {
        stack.push(children[index]);
      }
      continue;
    }
    if (String(node?.nodeType || "").trim() === "part") {
      leafParts.push(node);
    }
  }
  return leafParts;
}

export function flattenAssemblyNodes(root) {
  const nodes = [];
  const stack = root ? [root] : [];
  while (stack.length) {
    const node = stack.pop();
    nodes.push(node);
    const children = Array.isArray(node?.children) ? node.children : [];
    for (let index = children.length - 1; index >= 0; index -= 1) {
      stack.push(children[index]);
    }
  }
  return nodes;
}

export function findAssemblyNode(root, nodeId) {
  const normalizedNodeId = String(nodeId || "").trim();
  if (!root || !normalizedNodeId || normalizedNodeId === "root") {
    return root || null;
  }
  const stack = [root];
  while (stack.length) {
    const node = stack.pop();
    if (String(node?.id || "").trim() === normalizedNodeId) {
      return node;
    }
    const children = Array.isArray(node?.children) ? node.children : [];
    for (let index = children.length - 1; index >= 0; index -= 1) {
      stack.push(children[index]);
    }
  }
  return null;
}

export function rootAssemblyInspectionNodeId(root) {
  return String(root?.id || "").trim() || "root";
}

export function normalizeAssemblyInspectionNodeId(root, nodeId) {
  if (!root) {
    return "";
  }
  const rootId = rootAssemblyInspectionNodeId(root);
  const normalizedNodeId = String(nodeId || "").trim();
  if (!normalizedNodeId || normalizedNodeId === "root" || normalizedNodeId === rootId) {
    return rootId;
  }
  const node = findAssemblyNode(root, normalizedNodeId);
  return String(node?.id || "").trim() || rootId;
}

export function assemblyInspectionNode(root, nodeId) {
  if (!root) {
    return null;
  }
  return findAssemblyNode(root, normalizeAssemblyInspectionNodeId(root, nodeId)) || root;
}

function directChildAssemblyNodeIds(node) {
  return (Array.isArray(node?.children) ? node.children : [])
    .map((child) => String(child?.id || "").trim())
    .filter(Boolean);
}

export function selectableAssemblyNodeIdsForInspection(root, nodeId) {
  const inspectedNode = assemblyInspectionNode(root, nodeId);
  return directChildAssemblyNodeIds(inspectedNode);
}

export function treeSelectableAssemblyNodeIdsForInspection(root, nodeId) {
  const inspectedNode = assemblyInspectionNode(root, nodeId);
  return directChildAssemblyNodeIds(inspectedNode);
}

export function focusedLeafPartIdsForAssemblyInspection(root, nodeId) {
  const inspectedNodeId = normalizeAssemblyInspectionNodeId(root, nodeId);
  const rootId = rootAssemblyInspectionNodeId(root);
  if (!root || !inspectedNodeId || inspectedNodeId === rootId) {
    return [];
  }
  return descendantLeafPartIds(assemblyInspectionNode(root, inspectedNodeId));
}

export function descendantLeafPartIds(node) {
  return flattenAssemblyLeafParts(node)
    .map((part) => String(part?.id || "").trim())
    .filter(Boolean);
}

export function representativeAssemblyLeafPartId(node) {
  const nodeId = String(node?.id || "").trim();
  if (!node) {
    return "";
  }
  if (String(node?.nodeType || "").trim() === "part") {
    return nodeId;
  }
  const declaredLeafPartIds = Array.isArray(node?.leafPartIds)
    ? node.leafPartIds.map((id) => String(id || "").trim()).filter(Boolean)
    : [];
  if (declaredLeafPartIds.length) {
    return declaredLeafPartIds[0];
  }
  return descendantLeafPartIds(node)[0] || nodeId;
}

export function buildAssemblyLeafToNodePickMap(nodes) {
  const map = new Map();
  for (const node of Array.isArray(nodes) ? nodes : []) {
    const nodeId = String(node?.id || "").trim();
    if (!nodeId) {
      continue;
    }
    const leafPartIds = Array.isArray(node?.leafPartIds) && node.leafPartIds.length
      ? node.leafPartIds
      : descendantLeafPartIds(node);
    for (const leafPartId of leafPartIds) {
      const normalizedLeafPartId = String(leafPartId || "").trim();
      if (normalizedLeafPartId) {
        map.set(normalizedLeafPartId, nodeId);
      }
    }
  }
  return map;
}

export function resolveAssemblyPickedPartId(partId, {
  pickPartIdMap,
  validLeafPartIds = []
} = {}) {
  const normalizedPartId = String(partId || "").trim();
  if (!normalizedPartId) {
    return "";
  }
  const validLeafPartIdSet = validLeafPartIds instanceof Set
    ? validLeafPartIds
    : new Set(
      (Array.isArray(validLeafPartIds) ? validLeafPartIds : [])
        .map((id) => String(id || "").trim())
        .filter(Boolean)
    );
  const mappedPartId = pickPartIdMap instanceof Map
    ? String(pickPartIdMap.get(normalizedPartId) || "").trim()
    : "";
  if (mappedPartId) {
    return mappedPartId;
  }
  if (validLeafPartIdSet.size && validLeafPartIdSet.has(normalizedPartId)) {
    return normalizedPartId;
  }
  return mappedPartId || normalizedPartId;
}

export function leafPartIdsForAssemblySelection(partId, {
  assemblyPartMap,
  fallbackPartId = "",
  validLeafPartIds = []
} = {}) {
  const normalizedPartId = String(partId || "").trim();
  const validLeafPartIdSet = validLeafPartIds instanceof Set
    ? validLeafPartIds
    : new Set(
      (Array.isArray(validLeafPartIds) ? validLeafPartIds : [])
        .map((id) => String(id || "").trim())
        .filter(Boolean)
    );
  const leafIdIsValid = (id) => {
    return !validLeafPartIdSet.size || validLeafPartIdSet.has(id);
  };
  const normalizeLeafIds = (leafPartIds) => {
    const seen = new Set();
    const result = [];
    for (const leafPartId of Array.isArray(leafPartIds) ? leafPartIds : []) {
      const normalizedLeafPartId = String(leafPartId || "").trim();
      if (!normalizedLeafPartId || seen.has(normalizedLeafPartId) || !leafIdIsValid(normalizedLeafPartId)) {
        continue;
      }
      seen.add(normalizedLeafPartId);
      result.push(normalizedLeafPartId);
    }
    return result;
  };

  if (normalizedPartId) {
    const selectedNode = assemblyPartMap instanceof Map
      ? assemblyPartMap.get(normalizedPartId) || null
      : null;
    const selectedLeafPartIds = selectedNode
      ? normalizeLeafIds(descendantLeafPartIds(selectedNode))
      : normalizeLeafIds([normalizedPartId]);
    if (selectedLeafPartIds.length) {
      return selectedLeafPartIds;
    }
  }

  const normalizedFallbackPartId = String(fallbackPartId || "").trim();
  return normalizeLeafIds([normalizedFallbackPartId]);
}

export function assemblyBreadcrumb(root, nodeId) {
  const normalizedNodeId = String(nodeId || "").trim();
  if (!root) {
    return [];
  }
  const path = [];
  function visit(node) {
    path.push(node);
    if (!normalizedNodeId || normalizedNodeId === "root" || String(node?.id || "").trim() === normalizedNodeId) {
      return true;
    }
    for (const child of Array.isArray(node?.children) ? node.children : []) {
      if (visit(child)) {
        return true;
      }
    }
    path.pop();
    return false;
  }
  return visit(root) ? [...path] : [root];
}

function meshPartId(part) {
  return String(part?.occurrenceId || part?.id || "").trim();
}

function meshPartNumericValue(part, key) {
  return Math.max(0, Math.floor(Number(part?.[key]) || 0));
}

// --- Component-GLB package composition (design/component-glb-artifacts.md) -------
//
// Unlike the monolithic .step.glb (which bakes every occurrence transform into
// world-space vertices at export), a package's component GLBs are meshed once in
// their LOCAL frame and instanced N times by the assembly descriptor. So composition
// here must apply each occurrence's 16-float transform to the copied vertices — the
// step the self-contained path skips because the monolith was already world-baked.

function transformPointInto(out, base, matrix, x, y, z) {
  out[base] = matrix[0] * x + matrix[1] * y + matrix[2] * z + matrix[3];
  out[base + 1] = matrix[4] * x + matrix[5] * y + matrix[6] * z + matrix[7];
  out[base + 2] = matrix[8] * x + matrix[9] * y + matrix[10] * z + matrix[11];
}

function transformNormalInto(out, base, matrix, x, y, z) {
  // Rotation/scale only (no translation), then renormalize. For the rigid (and
  // mirror) transforms an assembly uses, the upper-3x3 carries direction correctly;
  // renormalizing absorbs any uniform scale.
  let nx = matrix[0] * x + matrix[1] * y + matrix[2] * z;
  let ny = matrix[4] * x + matrix[5] * y + matrix[6] * z;
  let nz = matrix[8] * x + matrix[9] * y + matrix[10] * z;
  const length = Math.hypot(nx, ny, nz);
  if (length > 1e-12) {
    nx /= length;
    ny /= length;
    nz /= length;
  } else {
    nx = x;
    ny = y;
    nz = z;
  }
  out[base] = nx;
  out[base + 1] = ny;
  out[base + 2] = nz;
}

function matrixDeterminant3(matrix) {
  return (
    matrix[0] * (matrix[5] * matrix[10] - matrix[6] * matrix[9]) -
    matrix[1] * (matrix[4] * matrix[10] - matrix[6] * matrix[8]) +
    matrix[2] * (matrix[4] * matrix[9] - matrix[5] * matrix[8])
  );
}

function componentMeshDataFor(componentMeshDataByCid, cid) {
  if (!componentMeshDataByCid) {
    return null;
  }
  if (typeof componentMeshDataByCid.get === "function") {
    return componentMeshDataByCid.get(cid) || null;
  }
  return componentMeshDataByCid[cid] || null;
}

/**
 * Compose a renderable meshData from an assembly-package descriptor plus a map of
 * already-parsed component meshDatas (one per unique component cid, each from
 * buildMeshDataFromGlbBuffer on its component GLB). Each occurrence's transform is
 * baked into the copied vertices/normals (partTransformsBaked: true), so the result
 * is drop-in for the same renderer path the monolithic .step.glb uses.
 *
 * Output parts carry occurrenceId = the assembly occurrence id and componentId =
 * the source component cid; sourcePartRanges keep the COMPONENT-LOCAL occurrenceId +
 * primitiveIndex so picks resolve against that component's own selector runtime
 * (the occurrence id then namespaces the resolved selector).
 */
export function buildComposedPackageMeshData(descriptor, componentMeshDataByCid) {
  const occurrences = Array.isArray(descriptor?.occurrences) ? descriptor.occurrences : [];
  if (!occurrences.length) {
    throw new Error("Assembly package descriptor has no occurrences");
  }

  const placements = [];
  let totalVertexCount = 0;
  let totalIndexCount = 0;
  let anyColors = false;
  const missingComponentIds = [];
  for (const occurrence of occurrences) {
    const cid = String(occurrence?.component || "").trim();
    const componentMeshData = componentMeshDataFor(componentMeshDataByCid, cid);
    const sourceParts = Array.isArray(componentMeshData?.parts) ? componentMeshData.parts : [];
    if (!componentMeshData || !sourceParts.length) {
      if (cid) {
        missingComponentIds.push(cid);
      }
      continue;
    }
    for (const sourcePart of sourceParts) {
      totalVertexCount += meshPartNumericValue(sourcePart, "vertexCount");
      totalIndexCount += meshPartNumericValue(sourcePart, "triangleCount") * 3;
    }
    const componentColors = componentMeshData?.colors;
    if (componentColors && componentColors.length === (componentMeshData?.vertices?.length || 0) && componentColors.length > 0) {
      anyColors = true;
    }
    // Clean components carry no per-vertex COLOR_0; their color rides on the occurrence's
    // `color` (a material baseColorFactor in the GLB). An occurrence override color must still
    // produce a colored composed mesh, so count it toward allocating the colors buffer.
    if (toVectorArray(occurrence?.color)) {
      anyColors = true;
    }
    placements.push({ occurrence, componentMeshData, sourceParts });
  }
  if (!placements.length) {
    throw new Error("Assembly package matched no renderable component GLBs");
  }

  const vertices = new Float32Array(totalVertexCount * 3);
  const normals = new Float32Array(totalVertexCount * 3);
  const colors = anyColors ? new Float32Array(totalVertexCount * 3) : new Float32Array(0);
  const indices = new Uint32Array(totalIndexCount);
  // Per-vertex edge attributes drive the wireframe-on-mesh edge shader (3 floats/vertex
  // barycentric + a per-vertex edge class). They are triangle-local, so they merge untransformed,
  // aligned with the composed vertices. Leaving them empty (the prior stub) is why assembly edges
  // never rendered.
  const edgeSample = placements.find(
    (placement) => (placement.componentMeshData?.surfaceEdgeBarycentric?.length || 0) > 0
  )?.componentMeshData || null;
  const hasEdges = !!edgeSample;
  const EdgeClassCtor = edgeSample?.surfaceEdgeClass ? edgeSample.surfaceEdgeClass.constructor : Uint8Array;
  const surfaceEdgeBarycentric = hasEdges ? new Float32Array(totalVertexCount * 3) : new Float32Array(0);
  const surfaceEdgeClass = hasEdges && edgeSample.surfaceEdgeClass
    ? new EdgeClassCtor(totalVertexCount * 3)
    : new EdgeClassCtor(0);
  const parts = [];
  let vertexOffset = 0;
  let indexOffset = 0;

  for (const { occurrence, componentMeshData, sourceParts } of placements) {
    // Component geometry loads in CAD units (mm) — the GLB loader un-scales meters back to mm —
    // and the occurrence transform is authored in mm, so it applies directly to place each
    // (local-frame) component. Components MUST be local for this to be correct under dedup.
    const matrix = toTransformArray(occurrence?.transform);
    const mirrored = matrixDeterminant3(matrix) < 0;
    const occurrenceId = String(occurrence?.id || "").trim();
    const cid = String(occurrence?.component || "").trim();
    const overrideColor = toVectorArray(occurrence?.color);
    const sourceVertices = componentMeshData?.vertices || new Float32Array(0);
    const sourceNormals = componentMeshData?.normals || new Float32Array(0);
    const sourceColors = componentMeshData?.colors || new Float32Array(0);
    const sourceIndices = componentMeshData?.indices || new Uint32Array(0);
    const sourceEdgeBary = componentMeshData?.surfaceEdgeBarycentric || null;
    const sourceEdgeClass = componentMeshData?.surfaceEdgeClass || null;
    const hasComponentColors = sourceColors.length === sourceVertices.length && sourceColors.length > 0;

    const partVertexOffset = vertexOffset;
    const partTriangleOffset = Math.floor(indexOffset / 3);
    const sourcePartRanges = [];
    let minX = Infinity, minY = Infinity, minZ = Infinity;
    let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;

    for (const sourcePart of sourceParts) {
      const srcVertexOffset = meshPartNumericValue(sourcePart, "vertexOffset");
      const srcVertexCount = meshPartNumericValue(sourcePart, "vertexCount");
      const srcTriangleOffset = meshPartNumericValue(sourcePart, "triangleOffset");
      const srcTriangleCount = meshPartNumericValue(sourcePart, "triangleCount");
      const rangeTriangleOffset = Math.floor(indexOffset / 3) - partTriangleOffset;
      sourcePartRanges.push({
        // The DESCRIPTOR occurrence id (not the component's internal mesh-part id) so it matches
        // the composed selector runtime's remapped occurrence id, letting buildGlbFaceIdsForPart
        // resolve render-mesh triangles to this occurrence's faces.
        occurrenceId: occurrenceId || meshPartId(sourcePart),
        primitiveIndex: meshPartNumericValue(sourcePart, "primitiveIndex"),
        triangleOffset: rangeTriangleOffset,
        triangleCount: srcTriangleCount
      });

      const baseVertexOffset = vertexOffset;
      for (let local = 0; local < srcVertexCount; local += 1) {
        const src = (srcVertexOffset + local) * 3;
        const dst = (vertexOffset + local) * 3;
        const x = sourceVertices[src];
        const y = sourceVertices[src + 1];
        const z = sourceVertices[src + 2];
        transformPointInto(vertices, dst, matrix, x, y, z);
        if (sourceNormals.length >= src + 3) {
          transformNormalInto(normals, dst, matrix, sourceNormals[src], sourceNormals[src + 1], sourceNormals[src + 2]);
        }
        if (anyColors) {
          if (overrideColor) {
            colors[dst] = overrideColor[0];
            colors[dst + 1] = overrideColor[1];
            colors[dst + 2] = overrideColor[2];
          } else if (hasComponentColors) {
            colors[dst] = sourceColors[src];
            colors[dst + 1] = sourceColors[src + 1];
            colors[dst + 2] = sourceColors[src + 2];
          }
        }
        if (hasEdges) {
          if (sourceEdgeBary && sourceEdgeBary.length >= src + 3) {
            surfaceEdgeBarycentric[dst] = sourceEdgeBary[src];
            surfaceEdgeBarycentric[dst + 1] = sourceEdgeBary[src + 1];
            surfaceEdgeBarycentric[dst + 2] = sourceEdgeBary[src + 2];
          }
          if (sourceEdgeClass && sourceEdgeClass.length >= src + 3) {
            surfaceEdgeClass[dst] = sourceEdgeClass[src];
            surfaceEdgeClass[dst + 1] = sourceEdgeClass[src + 1];
            surfaceEdgeClass[dst + 2] = sourceEdgeClass[src + 2];
          }
        }
        const wx = vertices[dst];
        const wy = vertices[dst + 1];
        const wz = vertices[dst + 2];
        if (wx < minX) minX = wx; if (wy < minY) minY = wy; if (wz < minZ) minZ = wz;
        if (wx > maxX) maxX = wx; if (wy > maxY) maxY = wy; if (wz > maxZ) maxZ = wz;
      }

      const srcIndexStart = srcTriangleOffset * 3;
      for (let tri = 0; tri < srcTriangleCount; tri += 1) {
        const a = sourceIndices[srcIndexStart + tri * 3] - srcVertexOffset + baseVertexOffset;
        const b = sourceIndices[srcIndexStart + tri * 3 + 1] - srcVertexOffset + baseVertexOffset;
        const c = sourceIndices[srcIndexStart + tri * 3 + 2] - srcVertexOffset + baseVertexOffset;
        // A mirror (negative determinant) reverses triangle winding once baked into
        // positions; flip back so front-faces stay consistent with the renderer.
        indices[indexOffset] = a;
        indices[indexOffset + 1] = mirrored ? c : b;
        indices[indexOffset + 2] = mirrored ? b : c;
        indexOffset += 3;
      }
      vertexOffset += srcVertexCount;
    }

    const bounds = Number.isFinite(minX)
      ? { min: [minX, minY, minZ], max: [maxX, maxY, maxZ] }
      : null;
    const firstSourcePart = sourceParts[0];
    const displayName = String(occurrence?.name || occurrenceId || cid || meshPartId(firstSourcePart)).trim();
    parts.push({
      id: occurrenceId || cid,
      occurrenceId: occurrenceId || cid,
      componentId: cid,
      name: displayName,
      label: displayName,
      nodeType: "part",
      transform: matrix,
      bounds,
      sourceBounds: bounds,
      color: overrideColor || firstSourcePart?.color || null,
      hasSourceColors: anyColors && (!!overrideColor || hasComponentColors),
      vertexOffset: partVertexOffset,
      vertexCount: vertexOffset - partVertexOffset,
      triangleOffset: partTriangleOffset,
      triangleCount: Math.floor(indexOffset / 3) - partTriangleOffset,
      sourcePartRanges,
      edgeIndexOffset: 0,
      edgeIndexCount: 0
    });
  }

  return {
    vertices,
    indices,
    normals,
    colors,
    surfaceEdgeBarycentric,
    surfaceEdgeClass,
    edge_indices: new Uint32Array(0),
    parts,
    assemblyRoot: buildPackageAssemblyRoot(descriptor, parts),
    bounds: mergeBounds(parts.map((part) => part.bounds)),
    assemblyMates: assemblyMatesFromTopology(descriptor),
    missingComponentIds,
    partTransformsBaked: true,
    has_source_colors: anyColors
  };
}

// The package descriptor records a flat list of occurrences (the assembly hierarchy is collapsed
// at emit time), so synthesize a one-level assembly tree — a root node whose children are the
// placed parts — so the viewer's structure tree is expandable and every occurrence is selectable.
function enrichPackageAssemblyNode(node, partById) {
  const rawChildren = Array.isArray(node?.children) ? node.children : [];
  const children = rawChildren.map((child) => enrichPackageAssemblyNode(child, partById));
  const nodeType = String(node?.nodeType || "").trim() || (children.length ? "subassembly" : "part");
  const id = String(node?.id || "").trim();
  const name = String(node?.name || node?.label || id).trim();
  const declaredLeafIds = Array.isArray(node?.leafPartIds)
    ? node.leafPartIds.map((leafId) => String(leafId || "").trim()).filter(Boolean)
    : [];
  const leafPartIds = declaredLeafIds.length
    ? declaredLeafIds
    : (children.length
      ? children.flatMap((child) => child.leafPartIds)
      : (id ? [id] : []));
  const out = { id, occurrenceId: id, name, label: name, nodeType, leafPartIds, children };
  if (nodeType === "part") {
    // Enrich the leaf with its composed render part (transform/bounds/color drive highlighting).
    const part = partById.get(id);
    if (part) {
      out.componentId = part.componentId;
      out.transform = part.transform;
      out.bounds = part.bounds;
      out.sourceBounds = part.sourceBounds;
      out.color = part.color;
    }
  } else {
    out.transform = [...IDENTITY_TRANSFORM];
    out.bounds = mergeBounds(children.map((child) => child.bounds));
  }
  return out;
}

function buildPackageAssemblyRoot(descriptor, parts) {
  // A single-component part has no internal assembly structure: it renders as a topology
  // tree (solids/faces/edges) exactly like a monolithic STEP part. Returning null lets
  // buildStepTreeRoot fall through to buildStepPartRoot instead of showing a spurious
  // one-node "assembly" wrapper (which the part view can't render → "No assembly tree").
  if (String(descriptor?.entryKind || "").trim() === "part") {
    return null;
  }
  const partList = Array.isArray(parts) ? parts : [];
  const partById = new Map(partList.map((part) => [String(part.id), part]));
  // Preferred: the nested hierarchy the descriptor records (subassembly grouping over leaves),
  // so the structure tree can drill into / isolate subassemblies just like a monolithic STEP.
  const descriptorRoot = descriptor?.assembly?.root;
  if (descriptorRoot && typeof descriptorRoot === "object") {
    return enrichPackageAssemblyNode(descriptorRoot, partById);
  }
  // Fallback (legacy descriptor without a hierarchy): a flat root over the placed parts.
  if (!partList.length) {
    return null;
  }
  const children = partList.map((part) => ({
    id: part.id,
    occurrenceId: part.occurrenceId,
    componentId: part.componentId,
    name: part.name,
    label: part.label,
    nodeType: "part",
    transform: part.transform,
    bounds: part.bounds,
    sourceBounds: part.sourceBounds,
    color: part.color,
    leafPartIds: [part.id],
    children: []
  }));
  const rootName = String(descriptor?.rootName || "").trim() || "assembly";
  return {
    id: rootName,
    name: rootName,
    label: rootName,
    nodeType: "assembly",
    transform: [...IDENTITY_TRANSFORM],
    bounds: mergeBounds(children.map((child) => child.bounds)),
    leafPartIds: children.map((child) => child.id),
    children
  };
}
