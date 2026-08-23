import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { Readable, Writable } from "node:stream";

import fsSync from "node:fs";

import {
  contentTypeForStaticAsset,
  createCadViewerApiMiddleware,
  createLocalAssetMiddleware,
  serveDistAsset,
} from "./httpHandlers.mjs";
import { createLocalAssetBackend } from "./localAssetBackend.mjs";
import { __setSafeOpenPreOpenHookForTests } from "./safeAssetOpen.mjs";


function createResponse() {
  const headers = new Map();
  return {
    statusCode: 0,
    body: "",
    setHeader(name, value) {
      headers.set(String(name).toLowerCase(), String(value));
    },
    getHeader(name) {
      return headers.get(String(name).toLowerCase());
    },
    end(body = "") {
      this.body = String(body);
    },
  };
}


function createWritableResponse() {
  const headers = new Map();
  const chunks = [];
  const response = new Writable({
    write(chunk, _encoding, callback) {
      chunks.push(Buffer.from(chunk));
      callback();
    },
  });
  response.statusCode = 200;
  response.setHeader = (name, value) => {
    headers.set(String(name).toLowerCase(), String(value));
  };
  response.getHeader = (name) => headers.get(String(name).toLowerCase());
  response.bodyText = () => Buffer.concat(chunks).toString("utf8");
  response.finished = new Promise((resolve) => {
    response.on("finish", resolve);
  });
  return response;
}

function createJsonRequest({
  method = "POST",
  url,
  body = {},
} = {}) {
  const req = Readable.from([JSON.stringify(body)]);
  req.method = method;
  req.url = url;
  return req;
}


test("CAD Viewer API middleware awaits async backend catalog reads", async () => {
  const middleware = createCadViewerApiMiddleware({
    backend: {
      readCatalog: async () => ({ schemaVersion: 4, entries: [{ file: "part.step" }] }),
    },
  });
  const req = { method: "GET", url: "/__cad/catalog" };
  const res = createResponse();
  let nextCalled = false;

  await middleware(req, res, () => {
    nextCalled = true;
  });

  assert.equal(nextCalled, false);
  assert.equal(res.statusCode, 200);
  assert.equal(res.getHeader("content-type"), "application/json; charset=utf-8");
  assert.deepEqual(JSON.parse(res.body), {
    schemaVersion: 4,
    entries: [{ file: "part.step" }],
  });
});

test("CAD Viewer API middleware activates request roots for file params", async () => {
  const calls = [];
  const resolvedRoots = [];
  const activatedRoots = [];
  const activatedRequests = [];
  const resolvedRoot = {
    dir: "/tmp/file-root",
    rootPath: "/tmp/file-root",
    rootName: "file-root",
  };
  const middleware = createCadViewerApiMiddleware({
    backend: {
      readCatalog: async (request) => {
        calls.push(request);
        return { schemaVersion: 4, entries: [] };
      },
      resolveRequestRoot: (request) => {
        resolvedRoots.push(request);
        return resolvedRoot;
      },
    },
    onCatalogActivated: (root, request) => {
      activatedRoots.push(root);
      activatedRequests.push(request);
    },
  });
  const req = {
    method: "GET",
    url: "/__cad/catalog?file=part.step",
  };
  const res = createResponse();
  let nextCalled = false;

  await middleware(req, res, () => {
    nextCalled = true;
  });

  assert.equal(nextCalled, false);
  assert.equal(res.statusCode, 200);
  assert.deepEqual(calls, [
    { rootDir: "", fileRef: "part.step" },
  ]);
  assert.deepEqual(resolvedRoots, [
    { rootDir: "", fileRef: "part.step" },
  ]);
  assert.deepEqual(activatedRoots, [resolvedRoot]);
  assert.deepEqual(activatedRequests, [
    { rootDir: "", fileRef: "part.step" },
  ]);
});


test("CAD Viewer API middleware activates directories without reading the catalog", async () => {
  const calls = [];
  const activatedRoots = [];
  const activatedRequests = [];
  const resolvedRoot = {
    dir: "/tmp/file-root",
    rootPath: "/tmp/file-root",
    rootName: "file-root",
  };
  let readCatalogCalls = 0;
  const middleware = createCadViewerApiMiddleware({
    backend: {
      readCatalog: async () => {
        readCatalogCalls += 1;
        return { schemaVersion: 4, entries: [] };
      },
      resolveRequestRoot: (request) => {
        calls.push(request);
        return resolvedRoot;
      },
    },
    serverInfo: ({ rootDir, fileRef }) => ({
      app: "cad-viewer",
      rootDir,
      fileRef,
      activeDirectories: [resolvedRoot],
    }),
    onDirectoryActivated: (root, request) => {
      activatedRoots.push(root);
      activatedRequests.push(request);
    },
  });
  const req = {
    method: "POST",
    url: "/__cad/directory/activate?dir=/tmp/file-root",
  };
  const res = createResponse();
  let nextCalled = false;

  await middleware(req, res, () => {
    nextCalled = true;
  });

  assert.equal(nextCalled, false);
  assert.equal(readCatalogCalls, 0);
  assert.equal(res.statusCode, 200);
  assert.deepEqual(calls, [
    { rootDir: "/tmp/file-root", fileRef: "" },
  ]);
  assert.deepEqual(activatedRoots, [resolvedRoot]);
  assert.deepEqual(activatedRequests, [
    { rootDir: "/tmp/file-root", fileRef: "" },
  ]);
  assert.deepEqual(JSON.parse(res.body), {
    ok: true,
    directory: resolvedRoot,
    server: {
      app: "cad-viewer",
      rootDir: "/tmp/file-root",
      fileRef: "",
      activeDirectories: [resolvedRoot],
    },
  });
});


test("CAD Viewer API middleware requires POST for directory activation", async () => {
  const middleware = createCadViewerApiMiddleware({
    backend: {
      resolveRoot: () => ({ dir: "/tmp/file-root", rootPath: "/tmp/file-root", rootName: "file-root" }),
      readCatalog: async () => ({ schemaVersion: 4, entries: [] }),
    },
  });
  const req = {
    method: "GET",
    url: "/__cad/directory/activate?dir=/tmp/file-root",
  };
  const res = createResponse();

  await middleware(req, res, () => {});

  assert.equal(res.statusCode, 405);
  assert.equal(res.getHeader("allow"), "POST");
  assert.match(JSON.parse(res.body).error, /POST/);
});


test("production static assets get browser-safe content types", () => {
  assert.equal(contentTypeForStaticAsset("dist/index.html"), "text/html; charset=utf-8");
  assert.equal(contentTypeForStaticAsset("dist/assets/index-abc.js"), "text/javascript; charset=utf-8");
  assert.equal(contentTypeForStaticAsset("dist/assets/index-abc.css"), "text/css; charset=utf-8");
  assert.equal(contentTypeForStaticAsset("dist/assets/module.wasm"), "application/wasm");
  assert.equal(contentTypeForStaticAsset("dist/assets/favicon.ico"), "image/x-icon");
});


test("production static assets are no-store and missing assets do not fall back to index", async () => {
  const distRoot = await fs.mkdtemp(path.join(os.tmpdir(), "cad-viewer-dist-"));
  await fs.mkdir(path.join(distRoot, "assets"), { recursive: true });
  await fs.writeFile(path.join(distRoot, "index.html"), "<main>CAD Viewer</main>");
  await fs.writeFile(path.join(distRoot, "assets", "index-abc.js"), "console.log('viewer');");
  const middleware = serveDistAsset({ distRoot });

  const assetResponse = createWritableResponse();
  let assetNextCalled = false;
  middleware({ url: "/assets/index-abc.js" }, assetResponse, () => {
    assetNextCalled = true;
  });
  await assetResponse.finished;
  assert.equal(assetNextCalled, false);
  assert.equal(assetResponse.statusCode, 200);
  assert.equal(assetResponse.getHeader("content-type"), "text/javascript; charset=utf-8");
  assert.equal(assetResponse.getHeader("cache-control"), "no-store");
  assert.equal(assetResponse.bodyText(), "console.log('viewer');");

  const missingAssetResponse = createWritableResponse();
  let missingNextCalled = false;
  middleware({ url: "/assets/old-hash.js" }, missingAssetResponse, () => {
    missingNextCalled = true;
  });
  await missingAssetResponse.finished;
  assert.equal(missingNextCalled, false);
  assert.equal(missingAssetResponse.statusCode, 404);
  assert.equal(missingAssetResponse.getHeader("content-type"), "text/plain; charset=utf-8");
  assert.equal(missingAssetResponse.getHeader("cache-control"), "no-store");
  assert.equal(missingAssetResponse.bodyText(), "Not found");

  const routeResponse = createWritableResponse();
  middleware({ url: "/project/tom" }, routeResponse, () => {});
  await routeResponse.finished;
  assert.equal(routeResponse.statusCode, 200);
  assert.equal(routeResponse.getHeader("content-type"), "text/html; charset=utf-8");
  assert.equal(routeResponse.getHeader("cache-control"), "no-store");
  assert.equal(routeResponse.bodyText(), "<main>CAD Viewer</main>");
});


test("CAD Viewer API middleware serves dynamic STEP source status", async () => {
  const calls = [];
  const middleware = createCadViewerApiMiddleware({
    backend: {
      readCatalog: async () => ({ schemaVersion: 4, entries: [{ file: "part.step" }] }),
      readStepSourceStatus: async (request) => {
        calls.push(request);
        return {
          ok: false,
          file: "part.step",
          sourceKind: "python",
          step: { status: "missing", missing: true, stale: false },
        };
      },
    },
  });
  const req = { method: "GET", url: "/__cad/step-source-status?file=part.step" };
  const res = createResponse();

  await middleware(req, res, () => {});

  assert.equal(res.statusCode, 200);
  assert.deepEqual(calls.map((call) => ({ fileRef: call.fileRef, hasCatalog: !!call.catalog })), [
    { fileRef: "part.step", hasCatalog: true },
  ]);
  assert.deepEqual(JSON.parse(res.body), {
    ok: false,
    file: "part.step",
    sourceKind: "python",
    step: { status: "missing", missing: true, stale: false },
  });
});


test("CAD Viewer API middleware serves local generation status", async () => {
  const middleware = createCadViewerApiMiddleware({
    rootDir: "models",
    backend: {
      readGenerationStatus: async ({ rootDir }) => ({
        schemaVersion: 1,
        rootDir,
        runs: [{ id: "run-1", files: ["part.step"] }],
        files: { "part.step": { running: true } },
      }),
    },
  });
  const req = { method: "GET", url: "/__cad/generation-status" };
  const res = createResponse();

  await middleware(req, res, () => {});

  assert.equal(res.statusCode, 200);
  assert.deepEqual(JSON.parse(res.body), {
    schemaVersion: 1,
    rootDir: "models",
    runs: [{ id: "run-1", files: ["part.step"] }],
    files: { "part.step": { running: true } },
  });
});

test("local asset middleware resolves legacy URDF mesh URLs from referrer file", async () => {
  const rootDir = await fs.mkdtemp(path.join(os.tmpdir(), "cad-viewer-assets-"));
  const robotDir = path.join(rootDir, "robots", "so101");
  const meshPath = path.join(robotDir, "meshes", "base.stl");
  await fs.mkdir(path.dirname(meshPath), { recursive: true });
  await fs.writeFile(meshPath, "solid base\nendsolid base\n");
  const calls = [];
  const middleware = createLocalAssetMiddleware({
    backend: {
      resolveRequestRoot: ({ rootDir: requestedRootDir, fileRef }) => {
        calls.push({ requestedRootDir, fileRef });
        return { dir: requestedRootDir, rootPath: requestedRootDir, rootName: path.basename(requestedRootDir) };
      },
      assetPathForFileRef: (fileRef) => fileRef,
      contentTypeForPath: () => "model/stl",
    },
  });
  const referrer = `http://127.0.0.1:4183/?dir=${encodeURIComponent(rootDir)}&file=${encodeURIComponent(path.join(robotDir, "so101.urdf"))}`;
  const req = {
    method: "GET",
    url: "/__cad/meshes/base.stl",
    headers: { referer: referrer },
  };
  const res = createWritableResponse();

  middleware(req, res, () => {
    assert.fail("expected legacy mesh path to be served");
  });
  await res.finished;

  assert.equal(res.statusCode, 200);
  assert.equal(res.getHeader("content-type"), "model/stl");
  assert.equal(res.bodyText(), "solid base\nendsolid base\n");
  assert.deepEqual(calls, [
    { requestedRootDir: rootDir, fileRef: meshPath },
  ]);
});


test("local asset middleware survives malformed percent-encoding in legacy CAD paths", async () => {
  const middleware = createLocalAssetMiddleware({
    backend: {
      assetPathForFileRef: () => {
        assert.fail("assetPathForFileRef must not be called for a malformed path");
      },
      contentTypeForPath: () => "model/stl",
    },
  });
  const req = {
    method: "GET",
    url: "/__cad/foo%zz.step",
    headers: {},
  };
  const res = createResponse();
  let nextCalled = false;

  middleware(req, res, () => {
    nextCalled = true;
  });

  assert.equal(nextCalled, true, "malformed legacy CAD path should fall through to the next middleware");
});


test("CAD Viewer API middleware reveals file assets with POST reveal route", async () => {
  const calls = [];
  const middleware = createCadViewerApiMiddleware({
    backend: {
      readCatalog: async () => ({ schemaVersion: 4, entries: [{ file: "part.step" }] }),
      openFileAsset: async (request) => {
        calls.push(request);
        return {
          asset: request.asset,
          file: "part.step",
          filename: "part.step",
          opened: true,
        };
      },
    },
  });
  const req = { method: "POST", url: "/__cad/reveal?file=part.step&asset=output" };
  const res = createResponse();

  await middleware(req, res, () => {});

  assert.equal(res.statusCode, 200);
  assert.deepEqual(calls.map((call) => ({ fileRef: call.fileRef, asset: call.asset, hasCatalog: !!call.catalog })), [
    { fileRef: "part.step", asset: "output", hasCatalog: true },
  ]);
  assert.deepEqual(JSON.parse(res.body), {
    ok: true,
    asset: "output",
    file: "part.step",
    filename: "part.step",
    opened: true,
  });
});


test("CAD Viewer API middleware downloads file asset bytes from hosted backends", async () => {
  const middleware = createCadViewerApiMiddleware({
    backend: {
      readCatalog: async () => ({ schemaVersion: 4, entries: [{ file: "part.step" }] }),
      readFileAsset: async ({ fileRef, asset }) => ({
        file: fileRef,
        asset,
        filename: "part.step",
        contentType: "application/step",
        body: Buffer.from("ISO-10303-21;"),
      }),
    },
  });
  const req = { method: "GET", url: "/__cad/download?file=part.step&asset=output" };
  const res = createResponse();

  await middleware(req, res, () => {});

  assert.equal(res.statusCode, 200);
  assert.equal(res.getHeader("content-type"), "application/step");
  assert.equal(res.getHeader("content-disposition"), "attachment; filename=\"part.step\"; filename*=UTF-8''part.step");
  assert.equal(res.body, "ISO-10303-21;");
});


test("CAD Viewer API middleware can redirect hosted downloads to direct asset URLs", async () => {
  const middleware = createCadViewerApiMiddleware({
    backend: {
      readCatalog: async () => ({ schemaVersion: 4, entries: [{ file: "part.step" }] }),
      resolveFileAssetAccess: async ({ fileRef, asset }) => ({
        file: fileRef,
        asset,
        filename: "part.step",
        url: "https://blob.example.test/models2/part.step",
      }),
      readFileAsset: async () => {
        throw new Error("hosted direct downloads should not proxy Blob bytes");
      },
    },
    preferFileDownloadRedirects: true,
  });
  const req = { method: "GET", url: "/__cad/download?file=part.step&asset=output" };
  const res = createResponse();

  await middleware(req, res, () => {});

  assert.equal(res.statusCode, 302);
  assert.equal(res.getHeader("location"), "https://blob.example.test/models2/part.step");
  assert.equal(res.getHeader("cache-control"), "no-store");
  assert.equal(res.body, "");
});


test("CAD Viewer API middleware rejects hosted reveal requests", async () => {
  const middleware = createCadViewerApiMiddleware({
    backend: {
      readCatalog: async () => ({ schemaVersion: 4, entries: [{ file: "part.step" }] }),
      readFileAsset: async () => ({
        filename: "part.step",
        body: Buffer.from("step"),
      }),
    },
  });
  const req = { method: "POST", url: "/__cad/reveal?file=part.step&asset=output" };
  const res = createResponse();

  await middleware(req, res, () => {});

  assert.equal(res.statusCode, 405);
  assert.match(JSON.parse(res.body).error, /local filesystem/);
});

test("CAD Viewer API middleware leaves the retired export route unclaimed", async () => {
  const middleware = createCadViewerApiMiddleware({
    backend: {
      readCatalog: async () => ({ schemaVersion: 4, entries: [] }),
    },
  });
  const req = createJsonRequest({ url: "/__cad/implicit-export" });
  const res = createResponse();
  let nextCalled = false;

  await middleware(req, res, () => {
    nextCalled = true;
  });

  assert.equal(nextCalled, true);
  assert.equal(res.body, "");
});


test("CAD Viewer API middleware leaves STEP artifact route unclaimed when generation is disabled", async () => {
  const middleware = createCadViewerApiMiddleware({
    backend: {
      readCatalog: async () => ({ schemaVersion: 4, entries: [] }),
    },
    enableStepArtifactBackend: false,
  });
  const req = { method: "POST", url: "/__cad/step-artifact?file=part.step" };
  const res = createResponse();
  let nextCalled = false;

  await middleware(req, res, () => {
    nextCalled = true;
  });

  assert.equal(nextCalled, true);
  assert.equal(res.body, "");
});


test("CAD Viewer API middleware can claim disabled STEP artifact routes with JSON", async () => {
  const middleware = createCadViewerApiMiddleware({
    backend: {
      readCatalog: async () => ({ schemaVersion: 4, entries: [] }),
    },
    enableStepArtifactBackend: false,
    claimDisabledStepArtifactRoute: true,
  });
  const req = { method: "POST", url: "/__cad/step-artifact?file=part.step" };
  const res = createResponse();
  let nextCalled = false;

  await middleware(req, res, () => {
    nextCalled = true;
  });

  assert.equal(nextCalled, false);
  assert.equal(res.statusCode, 501);
  assert.match(JSON.parse(res.body).error, /not enabled/);
});


test("CAD Viewer API middleware rejects non-filesystem STEP artifact backends", async () => {
  const calls = [];
  const middleware = createCadViewerApiMiddleware({
    backend: {
      readCatalog: async () => ({ schemaVersion: 4, entries: [{ file: "part.step" }] }),
      generateStepArtifact: async (request) => {
        calls.push(request);
        return {
          ok: true,
          entry: { file: "part.step", kind: "part", url: "/.part.step.glb", hash: "hash", bytes: 3 },
          result: { uploaded: true },
          catalog: {
            schemaVersion: 4,
            entries: [{ file: "part.step", kind: "part", url: "/.part.step.glb", hash: "hash", bytes: 3 }]
          },
        };
      },
    },
    enableStepArtifactBackend: true,
  });
  const req = { method: "POST", url: "/__cad/step-artifact?file=part.step&force=1" };
  const res = createResponse();

  await middleware(req, res, () => {});

  assert.equal(res.statusCode, 501);
  assert.deepEqual(calls, []);
  assert.deepEqual(JSON.parse(res.body), {
    error: "STEP artifact generation requires a local filesystem CAD Viewer backend",
  });
});


// P1 production local asset middleware integration: ``serveStaticFile``
// must take the capability-style (safeAssetOpen) branch for user-
// controlled assets. Interpose a swap between the segment walk and
// the ``open`` syscall via the shared preOpenHook. If ``trustedRoot``
// were NOT threaded, ``serveStaticFile`` would fall back to the
// legacy ``lstat + createReadStream(path)`` branch and never invoke
// the hook -- so hook firing proves the safe path is exercised, and
// the resulting 403 proves the swap was caught before bytes were
// served.
test("createLocalAssetMiddleware routes user assets through the safe handle path (interposed swap rejected)", async (t) => {
  const rootDir = await fs.mkdtemp(path.join(os.tmpdir(), "cad-mw-safe-"));
  t.after(async () => { await fs.rm(rootDir, { recursive: true, force: true }); });
  const insidePath = path.join(rootDir, "part.stl");
  await fs.writeFile(insidePath, "solid part\nendsolid part\n");
  const outsidePath = path.join(rootDir, "..", `cad-mw-outside-${process.pid}.stl`);
  await fs.writeFile(outsidePath, "SECRET\n");
  t.after(async () => { await fs.rm(outsidePath, { force: true }); });

  let hookFired = false;
  __setSafeOpenPreOpenHookForTests(() => {
    hookFired = true;
    // Deterministic swap: after the segment walk has captured the
    // legit inode, replace the leaf with a symlink pointing at the
    // outside secret. On POSIX ``O_NOFOLLOW`` will refuse the
    // symlink; either way the fstat-vs-lstat identity check catches
    // it.
    try { fsSync.unlinkSync(insidePath); } catch { /* race with cleanup */ }
    try { fsSync.symlinkSync(outsidePath, insidePath); }
    catch (error) { if (!error || (error.code !== "EPERM" && error.code !== "EACCES")) throw error; }
  });
  t.after(() => __setSafeOpenPreOpenHookForTests(null));
  // Detect unprivileged Windows sandboxes that cannot create
  // symlinks by first trying a probe symlink; if it fails we skip.
  const probeLink = path.join(rootDir, `probe-${process.pid}.link`);
  try {
    fsSync.symlinkSync(insidePath, probeLink);
    fsSync.unlinkSync(probeLink);
  } catch (error) {
    if (error && (error.code === "EPERM" || error.code === "EACCES")) {
      t.skip(`filesystem does not permit unprivileged symbolic links: ${error.message}`);
      return;
    }
    throw error;
  }

  const middleware = createLocalAssetMiddleware({
    backend: {
      // Minimal local-backend shim: resolveRequestRoot returns the
      // trusted root; assetPathForFileRef returns the validated path.
      resolveRequestRoot: () => ({ dir: rootDir, rootPath: rootDir, rootName: path.basename(rootDir) }),
      assetPathForFileRef: () => insidePath,
      contentTypeForPath: () => "model/stl",
    },
  });
  const req = {
    method: "GET",
    url: `/__cad/asset?file=${encodeURIComponent(insidePath)}&dir=${encodeURIComponent(rootDir)}`,
    headers: {},
  };
  const res = createWritableResponse();
  let nextCalled = false;
  middleware(req, res, () => { nextCalled = true; });
  // The safe path throws inside ``openAssetHandleUnderRoot`` and
  // returns via ``next()`` (404 not-found from safe-open branch) or
  // 403 from a containment error. Await the response event either way.
  await Promise.race([
    res.finished,
    new Promise((resolve) => setTimeout(resolve, 200)),
  ]);
  assert.equal(hookFired, true, "the safe handle path must have fired the preOpenHook (proves trustedRoot threaded)");
  const body = typeof res.bodyText === "function" ? res.bodyText() : "";
  assert.ok(!body.includes("SECRET"), `outside bytes must never be served, got ${JSON.stringify(body)}`);
  assert.ok(
    res.statusCode !== 200 || nextCalled,
    `swapped-asset request must be refused or fall through, got status=${res.statusCode} next=${nextCalled}`,
  );
});


test("local asset middleware serves a cadgen package descriptor and its component GLB", async (t) => {
  const directoryRoot = await fs.mkdtemp(path.join(os.tmpdir(), "cad-package-assets-"));
  t.after(async () => { await fs.rm(directoryRoot, { recursive: true, force: true }); });
  const modelRoot = path.join(directoryRoot, "models");
  const packageDir = path.join(modelRoot, "__cadgen__", "models", "part.py");
  const descriptorPath = path.join(packageDir, "assembly.json");
  const componentPath = path.join(packageDir, "components", "part.glb");
  await fs.mkdir(path.dirname(componentPath), { recursive: true });
  const descriptor = JSON.stringify({
    schemaVersion: 4,
    packageSchemaVersion: 3,
    components: { part: { glb: "components/part.glb", contentHash: "fixture" } },
  });
  const component = Buffer.from("glTFcomponent");
  await fs.writeFile(descriptorPath, descriptor);
  await fs.writeFile(componentPath, component);

  const backend = createLocalAssetBackend({ directoryRoot, rootDir: "models" });
  const middleware = createLocalAssetMiddleware({ backend });
  const requestAsset = async (filePath) => {
    const req = {
      method: "GET",
      url: `/__cad/asset?file=${encodeURIComponent(filePath)}&dir=${encodeURIComponent("models")}`,
      headers: {},
    };
    const res = createWritableResponse();
    let nextCalled = false;
    middleware(req, res, () => { nextCalled = true; });
    await Promise.race([
      res.finished,
      new Promise((resolve) => setTimeout(resolve, 1000)),
    ]);
    assert.equal(nextCalled, false, `${filePath} must be claimed by local asset middleware`);
    assert.equal(res.statusCode, 200, `${filePath} must be served`);
    return res;
  };

  const descriptorResponse = await requestAsset(descriptorPath);
  assert.equal(descriptorResponse.getHeader("content-type"), "application/json; charset=utf-8");
  assert.equal(descriptorResponse.bodyText(), descriptor);

  const componentResponse = await requestAsset(componentPath);
  assert.equal(componentResponse.getHeader("content-type"), "model/gltf-binary");
  assert.deepEqual(Buffer.from(componentResponse.bodyText()), component);
});
