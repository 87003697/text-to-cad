import assert from "node:assert/strict";
import test from "node:test";

import {
  buildRobotAssemblyRoot,
  robotVisualPartId,
  ROBOT_TREE_ROOT_ID
} from "./robotTree.js";
import { flattenAssemblyLeafParts, flattenAssemblyNodes } from "../assembly/meshData.js";

function visual(linkName, meshUrl) {
  return { meshUrl, label: `${linkName} visual` };
}

// A two-branch arm: base -> shoulder -> {elbow, sensor}. `sensor` has no visual, so it
// must not appear as a selectable node.
const URDF = {
  name: "test-arm",
  links: [
    { name: "base", visuals: [visual("base", "base.stl")] },
    { name: "shoulder", visuals: [visual("shoulder", "shoulder.stl")] },
    { name: "elbow", visuals: [visual("elbow", "elbow.stl")] },
    { name: "sensor", visuals: [] }
  ],
  joints: [
    { name: "j1", parent: "base", child: "shoulder", type: "revolute" },
    { name: "j2", parent: "shoulder", child: "elbow", type: "revolute" },
    { name: "j3", parent: "shoulder", child: "sensor", type: "fixed" }
  ]
};

const MESH_DATA = {
  parts: [
    { id: "base:base.stl", label: "base visual", bounds: { min: [0, 0, 0], max: [1, 1, 1] } },
    { id: "shoulder:shoulder.stl", label: "shoulder visual", bounds: { min: [0, 0, 1], max: [1, 1, 2] } },
    { id: "elbow:elbow.stl", label: "elbow visual", bounds: { min: [0, 0, 2], max: [1, 1, 3] } }
  ]
};

test("the link tree nests by joint and keeps the robot's own name", () => {
  const root = buildRobotAssemblyRoot({ urdfData: URDF, meshData: MESH_DATA, label: "so101.urdf" });
  assert.equal(root.displayName, "so101.urdf");
  // One root link, so the robot IS that link rather than a wrapper around it.
  assert.notEqual(root.id, ROBOT_TREE_ROOT_ID);
  assert.equal(root.linkName, "base");
  const shoulder = root.children.find((child) => child.linkName === "shoulder");
  assert.ok(shoulder, "shoulder must hang off base");
  assert.ok(
    shoulder.children.some((child) => child.linkName === "elbow"),
    "elbow must hang off shoulder"
  );
});

test("leaf part ids match the render parts, or a pick resolves to nothing", () => {
  const root = buildRobotAssemblyRoot({ urdfData: URDF, meshData: MESH_DATA });
  const leafIds = flattenAssemblyLeafParts(root).map((part) => part.id).sort();
  assert.deepEqual(leafIds, MESH_DATA.parts.map((part) => part.id).sort());
  assert.equal(robotVisualPartId("base", visual("base", "base.stl")), "base:base.stl");
});

test("a link with no renderable visual is not offered as a selectable node", () => {
  const root = buildRobotAssemblyRoot({ urdfData: URDF, meshData: MESH_DATA });
  const names = flattenAssemblyNodes(root).map((node) => node.linkName);
  assert.ok(!names.includes("sensor"), "a visual-less link has nothing to select");
});

test("assembly nodes carry the union of their descendants' bounds", () => {
  const root = buildRobotAssemblyRoot({ urdfData: URDF, meshData: MESH_DATA });
  assert.deepEqual(root.bbox.min, [0, 0, 0]);
  assert.deepEqual(root.bbox.max, [1, 1, 3]);
});

test("a joint cycle does not hang the tree", () => {
  // A malformed URDF naming a link its own ancestor must still render a robot.
  const cyclic = {
    ...URDF,
    joints: [
      { name: "j1", parent: "base", child: "shoulder" },
      { name: "j2", parent: "shoulder", child: "elbow" },
      { name: "j3", parent: "elbow", child: "base" }
    ]
  };
  const root = buildRobotAssemblyRoot({ urdfData: cyclic, meshData: MESH_DATA });
  assert.ok(root, "a cyclic robot still produces a tree");
  assert.ok(flattenAssemblyNodes(root).length <= 8, "the tree is finite");
});

test("several root links get one node to hang from", () => {
  const disjoint = { ...URDF, joints: [{ name: "j2", parent: "shoulder", child: "elbow" }] };
  const root = buildRobotAssemblyRoot({ urdfData: disjoint, meshData: MESH_DATA });
  assert.equal(root.id, ROBOT_TREE_ROOT_ID);
  assert.equal(root.children.length, 2);
});

test("no parts means no tree: an unloaded robot shows nothing to select", () => {
  assert.equal(buildRobotAssemblyRoot({ urdfData: URDF, meshData: { parts: [] } }), null);
  assert.equal(buildRobotAssemblyRoot({ urdfData: null, meshData: MESH_DATA }), null);
});
