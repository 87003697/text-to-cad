import assert from "node:assert/strict";
import test from "node:test";

import { resolvePackageAssetUrl, resolvePackageDirRef } from "./packageAssetUrl.js";

test("resolvePackageDirRef collapses self-contained and legacy refs", () => {
  assert.equal(
    resolvePackageDirRef("/models/simple/__cadcache__/models/part.step", "components/abc.glb"),
    "/models/simple/__cadcache__/models/part.step/components/abc.glb"
  );
  assert.equal(
    resolvePackageDirRef("/models/simple/__cadcache__/models/part.step", "../../components/abc.glb"),
    "/models/simple/__cadcache__/components/abc.glb"
  );
  assert.equal(resolvePackageDirRef("/models/part.step", "./assembly.json"), "/models/part.step/assembly.json");
});

test("resolvePackageAssetUrl repoints the file param on query-style (local-fs) URLs", () => {
  const meshUrl =
    "/__cad/asset?file=%2Fmodels%2Fsimple%2F__cadcache__%2Fmodels%2Fpart.step&dir=%2Fmodels&v=abc123";

  const descriptorUrl = resolvePackageAssetUrl(meshUrl, "assembly.json");
  const descriptor = new URL(descriptorUrl, "http://x.local");
  assert.equal(descriptor.searchParams.get("file"), "/models/simple/__cadcache__/models/part.step/assembly.json");
  // dir and v survive so the sub-asset stays scoped to the active root with a cache key.
  assert.equal(descriptor.searchParams.get("dir"), "/models");
  assert.equal(descriptor.searchParams.get("v"), "abc123");

  const componentUrl = resolvePackageAssetUrl(meshUrl, "components/7e4f.glb");
  const component = new URL(componentUrl, "http://x.local");
  assert.equal(
    component.searchParams.get("file"),
    "/models/simple/__cadcache__/models/part.step/components/7e4f.glb"
  );

  // A naive `${meshUrl}/assembly.json` would have left file at the directory; assert we did NOT.
  assert.notEqual(descriptor.searchParams.get("file"), "/models/simple/__cadcache__/models/part.step");
});

test("resolvePackageAssetUrl resolves against the path on path-style (blob) URLs", () => {
  const blobUrl = "https://blob.example.test/prefix/simple/__cadcache__/models/part.step";
  assert.equal(
    resolvePackageAssetUrl(blobUrl, "assembly.json"),
    "https://blob.example.test/prefix/simple/__cadcache__/models/part.step/assembly.json"
  );
  assert.equal(
    resolvePackageAssetUrl(blobUrl, "components/7e4f.glb"),
    "https://blob.example.test/prefix/simple/__cadcache__/models/part.step/components/7e4f.glb"
  );
});

test("resolvePackageAssetUrl returns empty for missing inputs", () => {
  assert.equal(resolvePackageAssetUrl("", "assembly.json"), "");
  assert.equal(resolvePackageAssetUrl("/__cad/asset?file=%2Fx", ""), "");
});
