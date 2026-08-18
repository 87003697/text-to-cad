import assert from "node:assert/strict";
import test from "node:test";

import {
  pathHasSuffix,
  refDisplayName,
  refDisplayNameCandidates,
  shortestUniquePathSuffix,
  shortestUniquePathSuffixes
} from "./filePathSuffix.js";

test("a filename that is unique in the tree is the whole suffix", () => {
  const suffixes = shortestUniquePathSuffixes([
    "models/step/assemblies/motorcycle_shock_absorber.step.py",
    "models/step/parts/print_in_place_hinge.step.py"
  ]);
  // `.step` is dropped from the displayed name; the `.py` that remains still says it is a
  // generator rather than a mesh.
  assert.equal(suffixes.get("models/step/assemblies/motorcycle_shock_absorber.step.py"), "motorcycle_shock_absorber.py");
  assert.equal(suffixes.get("models/step/parts/print_in_place_hinge.step.py"), "print_in_place_hinge.py");
});

test("format siblings stay distinct because the extension is kept", () => {
  // The reason the suffix is filename-with-extension rather than a bare stem: `mounting_plate`
  // exists four times in the repo and only the extension separates them.
  const paths = [
    "models/step/parts/mounting_plate.step.py",
    "models/mesh/stl/mounting_plate.stl",
    "models/mesh/3mf/mounting_plate.3mf",
    "models/mesh/glb/mounting_plate.glb"
  ];
  const suffixes = shortestUniquePathSuffixes(paths);
  assert.deepEqual(
    paths.map((path) => suffixes.get(path)),
    ["mounting_plate.py", "mounting_plate.stl", "mounting_plate.3mf", "mounting_plate.glb"]
  );
});

test("a genuinely colliding filename gains directory segments until it is unique", () => {
  // The real collisions in the repo: super_heavy.step.py, sts3250.step, link_assembly.step.py.
  const suffixes = shortestUniquePathSuffixes([
    "models/renders/starship/super_heavy.step.py",
    "models/renders/falcon_heavy/super_heavy.step.py",
    "models/step/parts/unique_part.step.py"
  ]);
  assert.equal(suffixes.get("models/renders/starship/super_heavy.step.py"), "starship/super_heavy.py");
  assert.equal(suffixes.get("models/renders/falcon_heavy/super_heavy.step.py"), "falcon_heavy/super_heavy.py");
  assert.equal(suffixes.get("models/step/parts/unique_part.step.py"), "unique_part.py");
});

test("three-way collisions keep growing until unique", () => {
  const suffixes = shortestUniquePathSuffixes([
    "a/shared/part.step",
    "b/shared/part.step",
    "c/shared/part.step"
  ]);
  // A raw STEP shows with no extension at all.
  assert.equal(suffixes.get("a/shared/part.step"), "a/shared/part");
  assert.equal(suffixes.get("b/shared/part.step"), "b/shared/part");
  assert.equal(suffixes.get("c/shared/part.step"), "c/shared/part");
});

test("adding a colliding file lengthens the incumbent's suffix", () => {
  // Emission is allowed to drift as the catalog changes; acceptance of longer spellings is what
  // must stay stable, which is why resolvers match any unambiguous suffix.
  const before = shortestUniquePathSuffixes(["models/a/plate.stl"]);
  assert.equal(before.get("models/a/plate.stl"), "plate.stl");
  const after = shortestUniquePathSuffixes(["models/a/plate.stl", "models/b/plate.stl"]);
  assert.equal(after.get("models/a/plate.stl"), "a/plate.stl");
});

test("edge inputs do not throw", () => {
  assert.equal(shortestUniquePathSuffixes([]).size, 0);
  assert.equal(shortestUniquePathSuffixes(null).size, 0);
  assert.equal(shortestUniquePathSuffixes(["plate.stl"]).get("plate.stl"), "plate.stl");
  assert.equal(shortestUniquePathSuffix("", ["a.stl"]), "");
  // A path outside the set still yields something usable rather than undefined.
  assert.equal(shortestUniquePathSuffix("other/x.stl", ["a.stl"]), "other/x.stl");
});

test("duplicate and windows-style paths normalize rather than colliding with themselves", () => {
  const suffixes = shortestUniquePathSuffixes([
    "models/a/plate.stl",
    "models/a/plate.stl",
    "models\\a\\plate.stl"
  ]);
  assert.equal(suffixes.size, 1, "the same path listed three ways is one entry");
  assert.equal(suffixes.get("models/a/plate.stl"), "plate.stl");
});

test("suffix matching is segment aligned, never substring", () => {
  assert.ok(pathHasSuffix("models/mesh/stl/mounting_plate.stl", "mounting_plate.stl"));
  assert.ok(pathHasSuffix("models/mesh/stl/mounting_plate.stl", "stl/mounting_plate.stl"));
  assert.ok(pathHasSuffix("models/a/plate.stl", "models/a/plate.stl"));
  // The bug this rule exists to prevent: a ref resolving to the wrong file by string luck.
  assert.ok(!pathHasSuffix("models/mesh/stl/mounting_plate.stl", "late.stl"));
  assert.ok(!pathHasSuffix("models/a/plate.stl", "b/plate.stl"));
  assert.ok(!pathHasSuffix("plate.stl", "models/a/plate.stl"), "suffix longer than the path");
  assert.ok(!pathHasSuffix("models/a/plate.stl", ""));
});

test("`.step` is dropped from the displayed name, other formats keep theirs", () => {
  assert.equal(refDisplayName("bracket.step.py"), "bracket.py");
  assert.equal(refDisplayName("bracket.stp.py"), "bracket.py");
  assert.equal(refDisplayName("bracket.step"), "bracket");
  assert.equal(refDisplayName("bracket.stp"), "bracket");
  // Meshes and drawings are not STEP, so their extension is the thing that identifies them.
  assert.equal(refDisplayName("plate.stl"), "plate.stl");
  assert.equal(refDisplayName("plate.3mf"), "plate.3mf");
  assert.equal(refDisplayName("plate.glb"), "plate.glb");
  assert.equal(refDisplayName("outline.dxf"), "outline.dxf");
  // A plain helper .py is already its own display name.
  assert.equal(refDisplayName("helper.py"), "helper.py");
});

test("a displayed name expands to every file it could have come from", () => {
  // Dropping `.step` means the name is no longer a literal path suffix, so resolving one back
  // to a file is an expansion rather than a match. This is the inverse the skill docs teach.
  assert.deepEqual(refDisplayNameCandidates("bracket.py"), ["bracket.py", "bracket.step.py", "bracket.stp.py"]);
  assert.deepEqual(refDisplayNameCandidates("bracket"), ["bracket", "bracket.step", "bracket.stp"]);
  assert.deepEqual(refDisplayNameCandidates("plate.stl"), ["plate.stl"]);
  assert.deepEqual(refDisplayNameCandidates(""), []);
});

test("suffix matching sees through the dropped .step", () => {
  // What the viewer emits must match the file it came from, or the CLI guard and the paste
  // round-trip both reject a legitimate ref.
  assert.ok(pathHasSuffix("models/step/parts/bracket.step.py", "bracket.py"));
  assert.ok(pathHasSuffix("models/step/parts/bracket.step", "bracket"));
  assert.ok(pathHasSuffix("models/step/parts/bracket.step.py", "parts/bracket.py"));
  assert.ok(!pathHasSuffix("models/step/parts/bracket.step.py", "other.py"));
});
