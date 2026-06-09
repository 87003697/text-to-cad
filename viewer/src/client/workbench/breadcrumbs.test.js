import assert from "node:assert/strict";
import test from "node:test";

import { buildSidebarDirectoryTree } from "./sidebar.js";
import {
  buildBreadcrumbNodes,
  collapsedBreadcrumbNodes
} from "./breadcrumbs.js";

test("breadcrumb dropdown directories target adjacent siblings", () => {
  const selectedEntry = {
    file: "assemblies/robot/arm/base.step",
    kind: "part",
    source: { format: "step", path: "assemblies/robot/arm/base.step" }
  };
  const tree = buildSidebarDirectoryTree([
    selectedEntry,
    {
      file: "assemblies/robot/arm/forearm.step",
      kind: "part",
      source: { format: "step", path: "assemblies/robot/arm/forearm.step" }
    },
    {
      file: "assemblies/robot/wrist.step",
      kind: "part",
      source: { format: "step", path: "assemblies/robot/wrist.step" }
    },
    {
      file: "assemblies/fixtures/clamp.step",
      kind: "part",
      source: { format: "step", path: "assemblies/fixtures/clamp.step" }
    },
    {
      file: "benchmarks/block.step",
      kind: "part",
      source: { format: "step", path: "benchmarks/block.step" }
    }
  ], { rootName: "models" });

  const nodes = buildBreadcrumbNodes({
    directoryTree: tree,
    selectedEntry,
    selectedFileLabel: "base.step",
    selectedFileTitle: "assemblies/robot/arm/base.step"
  });

  assert.deepEqual(
    nodes.map((node) => `${node.type}:${node.id || node.label}`),
    [
      "directory:assemblies",
      "directory:assemblies/robot",
      "directory:assemblies/robot/arm",
      "entry:base.step"
    ]
  );
  assert.equal(nodes[0].menuDirectory.id, "");
  assert.equal(nodes[1].menuDirectory.id, "assemblies");
  assert.equal(nodes[2].menuDirectory.id, "assemblies/robot");
  assert.equal(nodes[3].menuDirectory.id, "assemblies/robot/arm");
  assert.deepEqual(
    nodes[3].menuDirectory.entries.map((entry) => entry.file).sort(),
    [
      "assemblies/robot/arm/base.step",
      "assemblies/robot/arm/forearm.step"
    ]
  );
});

test("breadcrumb helpers support collapsed paths", () => {
  const items = collapsedBreadcrumbNodes([
    { label: "models" },
    { label: "assemblies" },
    { label: "robot" },
    { label: "arm" },
    { label: "base.step" }
  ]);

  assert.equal(items.length, 4);
  assert.equal(items[1].type, "ellipsis");
  assert.deepEqual(items[1].nodes.map((node) => node.label), ["assemblies", "robot"]);
});
