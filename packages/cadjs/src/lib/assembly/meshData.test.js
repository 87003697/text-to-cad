import assert from "node:assert/strict";
import { test } from "node:test";

import {
  assemblyBreadcrumb,
  assemblyInspectionNode,
  buildAssemblyLeafToNodePickMap,
  buildComposedPackageMeshData,
  descendantLeafPartIds,
  findAssemblyNode,
  focusedLeafPartIdsForAssemblyInspection,
  flattenAssemblyNodes,
  flattenAssemblyLeafParts,
  leafPartIdsForAssemblySelection,
  normalizeAssemblyInspectionNodeId,
  representativeAssemblyLeafPartId,
  rootAssemblyInspectionNodeId,
  selectableAssemblyNodeIdsForInspection,
  treeSelectableAssemblyNodeIdsForInspection,
  resolveAssemblyPickedPartId
} from "./meshData.js";

test("assembly helpers navigate nested assemblies down to leaf parts", () => {
  const root = {
    id: "root",
    nodeType: "assembly",
    displayName: "sample_root",
    children: [
      {
        id: "sample_module",
        nodeType: "assembly",
        displayName: "sample_module",
        children: [
          {
            id: "sample_part",
            nodeType: "part",
            displayName: "sample_part",
            children: []
          }
        ]
      }
    ]
  };

  assert.deepEqual(flattenAssemblyLeafParts(root).map((part) => part.id), ["sample_part"]);
  assert.deepEqual(flattenAssemblyNodes(root).map((node) => node.id), ["root", "sample_module", "sample_part"]);
  assert.equal(findAssemblyNode(root, "sample_module")?.displayName, "sample_module");
  assert.deepEqual(assemblyBreadcrumb(root, "sample_part").map((node) => node.id), ["root", "sample_module", "sample_part"]);
  assert.deepEqual(descendantLeafPartIds(root.children[0]), ["sample_part"]);
  assert.equal(representativeAssemblyLeafPartId(root.children[0]), "sample_part");
});

test("assembly picking maps rendered leaves to scoped assembly nodes", () => {
  const root = {
    id: "root",
    nodeType: "assembly",
    children: [
      {
        id: "module",
        occurrenceId: "o1.1",
        nodeType: "assembly",
        leafPartIds: ["leaf_a", "leaf_b"],
        children: [
          {
            id: "leaf_a",
            occurrenceId: "o1.1.1",
            nodeType: "part",
            sourcePath: "parts/a.step",
            children: []
          },
          {
            id: "leaf_b",
            occurrenceId: "o1.1.2",
            nodeType: "part",
            children: []
          }
        ]
      }
    ]
  };

  const pickPartIdMap = buildAssemblyLeafToNodePickMap(root.children);
  assert.deepEqual(
    [...pickPartIdMap.entries()],
    [
      ["leaf_a", "module"],
      ["leaf_b", "module"]
    ]
  );
  assert.equal(
    resolveAssemblyPickedPartId("leaf_a", {
      pickPartIdMap,
      validLeafPartIds: ["leaf_a", "leaf_b"]
    }),
    "module"
  );
  assert.equal(
    resolveAssemblyPickedPartId("legacy_mesh_leaf", {
      pickPartIdMap: new Map([["legacy_mesh_leaf", "module"]]),
      validLeafPartIds: ["leaf_a", "leaf_b"]
    }),
    "module"
  );
  const assemblyPartMap = new Map(flattenAssemblyNodes(root).map((node) => [node.id, node]));
  assert.deepEqual(
    leafPartIdsForAssemblySelection("module", {
      assemblyPartMap,
      fallbackPartId: "leaf_a",
      validLeafPartIds: ["leaf_a", "leaf_b"]
    }),
    ["leaf_a", "leaf_b"]
  );
  assert.deepEqual(
    leafPartIdsForAssemblySelection("leaf_a", {
      assemblyPartMap,
      validLeafPartIds: ["leaf_a", "leaf_b"]
    }),
    ["leaf_a"]
  );
  assert.deepEqual(
    leafPartIdsForAssemblySelection("missing", {
      assemblyPartMap,
      fallbackPartId: "leaf_b",
      validLeafPartIds: ["leaf_a", "leaf_b"]
    }),
    ["leaf_b"]
  );
  assert.equal(representativeAssemblyLeafPartId(root.children[0]), "leaf_a");
});

test("nested assembly selection resolves descendant render leaves without loading sibling topology", () => {
  const root = {
    id: "root",
    nodeType: "assembly",
    children: [
      {
        id: "outer",
        nodeType: "assembly",
        children: [
          {
            id: "inner",
            nodeType: "assembly",
            children: [
              {
                id: "leaf_a",
                nodeType: "part",
                occurrenceId: "o1.1.1.1",
                children: []
              },
              {
                id: "leaf_b",
                nodeType: "part",
                occurrenceId: "o1.1.1.2",
                children: []
              }
            ]
          }
        ]
      },
      {
        id: "sibling_leaf",
        nodeType: "part",
        occurrenceId: "o1.2",
        children: []
      }
    ]
  };
  const assemblyPartMap = new Map(flattenAssemblyNodes(root).map((node) => [node.id, node]));
  const validLeafPartIds = flattenAssemblyLeafParts(root).map((node) => node.id);

  assert.deepEqual(
    leafPartIdsForAssemblySelection("inner", {
      assemblyPartMap,
      validLeafPartIds
    }),
    ["leaf_a", "leaf_b"]
  );
  assert.deepEqual(
    leafPartIdsForAssemblySelection("outer", {
      assemblyPartMap,
      validLeafPartIds
    }),
    ["leaf_a", "leaf_b"]
  );
  assert.deepEqual(
    leafPartIdsForAssemblySelection("root", {
      assemblyPartMap,
      validLeafPartIds
    }),
    ["leaf_a", "leaf_b", "sibling_leaf"]
  );
});

test("assembly inspection helpers keep one inspected node and limit selectable children", () => {
  const root = {
    id: "root",
    nodeType: "assembly",
    children: [
      {
        id: "module",
        nodeType: "assembly",
        children: [
          {
            id: "leaf_a",
            nodeType: "part",
            children: []
          },
          {
            id: "leaf_b",
            nodeType: "part",
            children: []
          }
        ]
      },
      {
        id: "compound_part",
        nodeType: "part",
        children: [
          {
            id: "leaf_c",
            nodeType: "part",
            children: []
          },
          {
            id: "leaf_d",
            nodeType: "part",
            children: []
          }
        ]
      },
      {
        id: "sibling",
        nodeType: "part",
        children: []
      }
    ]
  };

  assert.equal(rootAssemblyInspectionNodeId(root), "root");
  assert.equal(normalizeAssemblyInspectionNodeId(root, ""), "root");
  assert.equal(normalizeAssemblyInspectionNodeId(root, "missing"), "root");
  assert.equal(normalizeAssemblyInspectionNodeId(root, "leaf_a"), "leaf_a");
  assert.equal(assemblyInspectionNode(root, "module")?.id, "module");

  assert.deepEqual(selectableAssemblyNodeIdsForInspection(root, ""), ["module", "compound_part", "sibling"]);
  assert.deepEqual(selectableAssemblyNodeIdsForInspection(root, "module"), ["leaf_a", "leaf_b"]);
  assert.deepEqual(selectableAssemblyNodeIdsForInspection(root, "compound_part"), ["leaf_c", "leaf_d"]);
  assert.deepEqual(selectableAssemblyNodeIdsForInspection(root, "leaf_a"), []);
  assert.equal(selectableAssemblyNodeIdsForInspection(root, "module").includes("sibling"), false);

  assert.deepEqual(treeSelectableAssemblyNodeIdsForInspection(root, ""), ["module", "compound_part", "sibling"]);
  assert.deepEqual(treeSelectableAssemblyNodeIdsForInspection(root, "module"), ["leaf_a", "leaf_b"]);
  assert.deepEqual(treeSelectableAssemblyNodeIdsForInspection(root, "compound_part"), ["leaf_c", "leaf_d"]);
  assert.deepEqual(treeSelectableAssemblyNodeIdsForInspection(root, "leaf_a"), []);
  assert.equal(treeSelectableAssemblyNodeIdsForInspection(root, "module").includes("sibling"), false);

  assert.deepEqual(focusedLeafPartIdsForAssemblyInspection(root, ""), []);
  assert.deepEqual(focusedLeafPartIdsForAssemblyInspection(root, "module"), ["leaf_a", "leaf_b"]);
  assert.deepEqual(focusedLeafPartIdsForAssemblyInspection(root, "compound_part"), ["leaf_c", "leaf_d"]);
  assert.deepEqual(focusedLeafPartIdsForAssemblyInspection(root, "leaf_a"), ["leaf_a"]);
});

test("assembly picking maps rendered leaves to the current selectable node before accepting leaf ids", () => {
  const pickPartIdMap = new Map([
    ["leaf_a", "module"],
    ["leaf_b", "module"],
    ["sibling", "sibling"]
  ]);
  const validLeafPartIds = ["leaf_a", "leaf_b", "sibling"];

  assert.equal(
    resolveAssemblyPickedPartId("leaf_a", { pickPartIdMap, validLeafPartIds }),
    "module"
  );
  assert.equal(
    resolveAssemblyPickedPartId("sibling", { pickPartIdMap, validLeafPartIds }),
    "sibling"
  );
  assert.equal(
    resolveAssemblyPickedPartId("unknown", { pickPartIdMap, validLeafPartIds }),
    "unknown"
  );
});

function unitTriangleComponentMeshData() {
  // One part: a triangle in the component's LOCAL frame, +z normals.
  return {
    vertices: new Float32Array([
      0, 0, 0,
      1, 0, 0,
      0, 1, 0
    ]),
    normals: new Float32Array([
      0, 0, 1,
      0, 0, 1,
      0, 0, 1
    ]),
    colors: new Float32Array(0),
    indices: new Uint32Array([0, 1, 2]),
    parts: [
      {
        id: "o1",
        occurrenceId: "o1",
        primitiveIndex: 0,
        vertexOffset: 0,
        vertexCount: 3,
        triangleOffset: 0,
        triangleCount: 1
      }
    ]
  };
}

const IDENTITY_4X4 = [
  1, 0, 0, 0,
  0, 1, 0, 0,
  0, 0, 1, 0,
  0, 0, 0, 1
];

test("composed package mesh bakes each occurrence transform into copied vertices", () => {
  const descriptor = {
    schemaVersion: 1,
    kind: "assembly-package",
    rootName: "demo",
    components: { cA: { glb: "components/cA.glb", contentHash: "abc" } },
    occurrences: [
      { id: "o1.1", name: "part_a", component: "cA", transform: IDENTITY_4X4 },
      {
        id: "o1.2",
        name: "part_b",
        component: "cA",
        transform: [
          1, 0, 0, 10,
          0, 1, 0, 0,
          0, 0, 1, 0,
          0, 0, 0, 1
        ]
      }
    ]
  };
  const composed = buildComposedPackageMeshData(descriptor, { cA: unitTriangleComponentMeshData() });

  assert.equal(composed.parts.length, 2);
  assert.equal(composed.partTransformsBaked, true);
  assert.equal(composed.vertices.length, 18); // 2 occ * 3 verts * 3
  assert.equal(composed.indices.length, 6);

  // o1.1 (identity) keeps local positions.
  assert.deepEqual([...composed.vertices.slice(0, 9)], [0, 0, 0, 1, 0, 0, 0, 1, 0]);
  // o1.2 is shifted +10 on x.
  assert.deepEqual([...composed.vertices.slice(9, 18)], [10, 0, 0, 11, 0, 0, 10, 1, 0]);

  // Each output part carries the assembly occurrence id + component id, and the pick range uses
  // the SAME assembly occurrence id so it matches the composed selector runtime's remapped
  // occurrence id (letting buildGlbFaceIdsForPart resolve render-mesh triangles to this
  // occurrence's faces). The component-local primitive index is preserved for the run lookup.
  assert.equal(composed.parts[1].occurrenceId, "o1.2");
  assert.equal(composed.parts[1].componentId, "cA");
  assert.equal(composed.parts[1].sourcePartRanges[0].occurrenceId, "o1.2");
  assert.equal(composed.parts[1].sourcePartRanges[0].primitiveIndex, 0);
  // Second part's indices are rebased onto the second vertex block.
  assert.deepEqual([...composed.indices.slice(3, 6)], [3, 4, 5]);
  // Bounds reflect the world-baked positions.
  assert.deepEqual(composed.parts[1].bounds, { min: [10, 0, 0], max: [11, 1, 0] });
});

test("composed package mesh flips winding for a mirrored occurrence", () => {
  const descriptor = {
    occurrences: [
      {
        id: "o1.1",
        name: "mirror",
        component: "cA",
        transform: [
          -1, 0, 0, 0,
          0, 1, 0, 0,
          0, 0, 1, 0,
          0, 0, 0, 1
        ]
      }
    ]
  };
  const composed = buildComposedPackageMeshData(descriptor, { cA: unitTriangleComponentMeshData() });
  // det < 0 => winding reversed from [0,1,2] to [0,2,1].
  assert.deepEqual([...composed.indices], [0, 2, 1]);
  // x mirrored.
  assert.deepEqual([...composed.vertices.slice(0, 9)], [0, 0, 0, -1, 0, 0, 0, 1, 0]);
});

test("composed package mesh records missing components instead of throwing", () => {
  const descriptor = {
    occurrences: [
      { id: "o1.1", name: "present", component: "cA", transform: IDENTITY_4X4 },
      { id: "o1.2", name: "absent", component: "cMissing", transform: IDENTITY_4X4 }
    ]
  };
  const composed = buildComposedPackageMeshData(descriptor, { cA: unitTriangleComponentMeshData() });
  assert.equal(composed.parts.length, 1);
  assert.deepEqual(composed.missingComponentIds, ["cMissing"]);
});

test("single-component part carries NO assemblyRoot so the viewer renders a topology tree", () => {
  // entryKind:"part" is a single-component package: the viewer must render it like a monolithic
  // STEP part (topology tree of solids/faces/edges), NOT a one-node assembly wrapper. Returning a
  // synthesized assemblyRoot would make buildStepTreeRoot show "No assembly tree" in the part view.
  const partDescriptor = {
    kind: "assembly-package",
    entryKind: "part",
    rootName: "bracket",
    components: { cA: { glb: "components/cA.glb", contentHash: "abc" } },
    occurrences: [{ id: "o1.1", name: "bracket", component: "cA", transform: IDENTITY_4X4 }]
  };
  const part = buildComposedPackageMeshData(partDescriptor, { cA: unitTriangleComponentMeshData() });
  assert.equal(part.parts.length, 1, "the single component still composes a render part");
  assert.equal(part.assemblyRoot, null, "a part has no assembly structure tree");

  // An assembly with the same single occurrence DOES synthesize a root (structure tree).
  const assemblyDescriptor = { ...partDescriptor, entryKind: "assembly" };
  const assembly = buildComposedPackageMeshData(assemblyDescriptor, { cA: unitTriangleComponentMeshData() });
  assert.ok(assembly.assemblyRoot, "an assembly keeps its structure tree");
  assert.equal(assembly.assemblyRoot.nodeType, "assembly");
});
