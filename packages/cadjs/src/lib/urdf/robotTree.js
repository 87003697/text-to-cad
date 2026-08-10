// A robot's LINK TREE, in the shape the viewer's assembly machinery already speaks.
//
// A URDF is a tree of links joined by joints — the most literal assembly structure in the
// repo, and the direct analogue of a STEP assembly tree. The viewer already builds mesh
// PARTS for a robot (one per link visual, see buildUrdfMeshGeometry), but published no
// tree over them, so the shared Tree panel, selection, hide, isolate and
// zoom-to-fit-selection never mounted and a robot's links were unreachable.
//
// This produces the same node shape `buildStepTreeRoot` returns, so every one of those
// features works against a robot without a single robot-specific branch:
//
//   { id, occurrenceId, nodeType: "assembly" | "part", displayName, name,
//     leafPartIds, bbox, children }
//
// Leaf part ids MUST match `meshData.parts[].id` or a pick resolves to nothing; both sides
// derive them from the same visual, so the rule lives here as `robotVisualPartId`.

import { mergeBounds } from "./kinematics.js";

export const ROBOT_TREE_ROOT_ID = "__robot__";

function normalizeString(value) {
  return String(value ?? "").trim();
}

// The id a link visual takes as a render part. Mirrors buildUrdfMeshGeometry's part id
// exactly — an explicit `visual.id` when the parser gave one, else link + mesh.
export function robotVisualPartId(linkName, visual) {
  const declaredId = normalizeString(visual?.id);
  if (declaredId) {
    return declaredId;
  }
  const meshRef = normalizeString(visual?.meshUrl) || normalizeString(visual?.partFileRef);
  return `${normalizeString(linkName)}:${meshRef}`;
}

function visualPartNodes(link, partsById) {
  const linkName = normalizeString(link?.name);
  const visuals = Array.isArray(link?.visuals) ? link.visuals : [];
  const nodes = [];
  for (const visual of visuals) {
    const partId = robotVisualPartId(linkName, visual);
    const part = partsById.get(partId);
    if (!part) {
      // The visual produced no renderable mesh (unresolved file, unsupported primitive).
      // Skipping keeps the tree honest: every node in it can be selected and framed.
      continue;
    }
    const label = normalizeString(part.label || part.name) || linkName;
    nodes.push({
      id: partId,
      occurrenceId: "",
      nodeType: "part",
      displayName: label,
      name: label,
      sourceKind: "robot-visual",
      linkName,
      leafPartIds: [partId],
      bbox: part.bounds || null,
      children: []
    });
  }
  return nodes;
}

function collectLeafPartIds(node) {
  if (!node) {
    return [];
  }
  if (node.nodeType === "part") {
    return [node.id];
  }
  return (node.children || []).flatMap(collectLeafPartIds);
}

function collectBbox(node) {
  if (!node) {
    return null;
  }
  if (node.nodeType === "part") {
    return node.bbox || null;
  }
  return mergeBounds((node.children || []).map(collectBbox).filter(Boolean));
}

// Build the link tree. `meshData` supplies the render parts a link's visuals resolved to;
// without it there is nothing selectable and the tree is not worth showing.
export function buildRobotAssemblyRoot({ urdfData = null, meshData = null, label = "" } = {}) {
  const links = Array.isArray(urdfData?.links) ? urdfData.links : [];
  const joints = Array.isArray(urdfData?.joints) ? urdfData.joints : [];
  const parts = Array.isArray(meshData?.parts) ? meshData.parts : [];
  if (!links.length || !parts.length) {
    return null;
  }

  const partsById = new Map();
  for (const part of parts) {
    const partId = normalizeString(part?.id);
    if (partId) {
      partsById.set(partId, part);
    }
  }

  // Parent link per child link, from the joints. A link with no parent joint is a root;
  // a well-formed URDF has exactly one, but a malformed or multi-tree file may have
  // several and both are handled the same way.
  const parentByChild = new Map();
  for (const joint of joints) {
    const child = normalizeString(joint?.child);
    const parent = normalizeString(joint?.parent);
    if (child && parent && !parentByChild.has(child)) {
      parentByChild.set(child, parent);
    }
  }

  const nodeByLink = new Map();
  for (const link of links) {
    const linkName = normalizeString(link?.name);
    if (!linkName) {
      continue;
    }
    nodeByLink.set(linkName, {
      id: `link:${linkName}`,
      occurrenceId: "",
      nodeType: "assembly",
      displayName: linkName,
      name: linkName,
      sourceKind: "robot-link",
      linkName,
      leafPartIds: [],
      bbox: null,
      children: visualPartNodes(link, partsById)
    });
  }

  // Nest by joint, guarding against a cycle: a URDF that names a link its own ancestor
  // would otherwise build an infinite tree and hang the viewer rather than draw a robot.
  const roots = [];
  for (const [linkName, node] of nodeByLink) {
    const parentName = parentByChild.get(linkName);
    const parentNode = parentName ? nodeByLink.get(parentName) : null;
    if (!parentNode || parentNode === node || isAncestor(node, parentNode, parentByChild, nodeByLink)) {
      roots.push(node);
      continue;
    }
    parentNode.children.push(node);
  }

  // A link with no visuals of its own and no children carries nothing to select. Prune
  // first, THEN roll up — pruning returns fresh nodes, so a rollup over the originals
  // would leave every assembly node's bbox and leafPartIds on the discarded copies.
  const prunedRoots = roots.map(pruneEmpty).filter(Boolean).map(rollUp);
  if (!prunedRoots.length) {
    return null;
  }

  const rootLabel = normalizeString(label) || normalizeString(urdfData?.name) || "Robot";
  // A single root link IS the robot; wrapping it in another node would add a tier that
  // says nothing. Several roots (a malformed or multi-tree file) need one to hang from.
  if (prunedRoots.length === 1) {
    return { ...prunedRoots[0], displayName: rootLabel, name: rootLabel };
  }
  return {
    id: ROBOT_TREE_ROOT_ID,
    occurrenceId: "",
    nodeType: "assembly",
    displayName: rootLabel,
    name: rootLabel,
    sourceKind: "robot",
    linkName: "",
    leafPartIds: prunedRoots.flatMap(collectLeafPartIds),
    bbox: mergeBounds(prunedRoots.map(collectBbox).filter(Boolean)),
    children: prunedRoots
  };
}

function isAncestor(candidate, node, parentByChild, nodeByLink) {
  let current = node;
  const seen = new Set();
  while (current) {
    if (current === candidate) {
      return true;
    }
    const currentName = normalizeString(current.linkName);
    if (!currentName || seen.has(currentName)) {
      return false;
    }
    seen.add(currentName);
    const parentName = parentByChild.get(currentName);
    current = parentName ? nodeByLink.get(parentName) : null;
  }
  return false;
}

function pruneEmpty(node) {
  if (!node || node.nodeType === "part") {
    return node;
  }
  const children = (node.children || []).map(pruneEmpty).filter(Boolean);
  if (!children.length) {
    return null;
  }
  return { ...node, children };
}

// Bottom-up: an assembly node's leaf parts and bounds are its descendants'.
function rollUp(node) {
  if (!node || node.nodeType === "part") {
    return node;
  }
  const children = (node.children || []).map(rollUp);
  return {
    ...node,
    children,
    leafPartIds: children.flatMap(collectLeafPartIds),
    bbox: mergeBounds(children.map(collectBbox).filter(Boolean))
  };
}
