import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import {
  fetchOpenInTargets,
  normalizedOpenInTargets,
  openInBusyKey,
  openInRequestUrl,
  requestOpenInApp
} from "./openInApps.js";

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
});

function jsonResponse(payload, { ok = true, status = 200 } = {}) {
  return {
    ok,
    status,
    json: async () => payload,
  };
}

test("normalizedOpenInTargets keeps only well-formed targets", () => {
  const targets = normalizedOpenInTargets({
    targets: [
      { id: "freecad", name: "FreeCAD", installed: true },
      { id: "fusion360", name: "Autodesk Fusion", installed: "yes" },
      { id: "", name: "Nameless" },
      { name: "No id" },
      null,
    ],
  });
  assert.deepEqual(targets, [
    { id: "freecad", name: "FreeCAD", installed: true },
    { id: "fusion360", name: "Autodesk Fusion", installed: false },
  ]);
});

test("normalizedOpenInTargets tolerates malformed payloads", () => {
  assert.deepEqual(normalizedOpenInTargets(null), []);
  assert.deepEqual(normalizedOpenInTargets({ targets: "nope" }), []);
});

test("openInBusyKey needs both parts", () => {
  assert.equal(openInBusyKey("/models/part.step", "freecad"), "/models/part.step:open-in:freecad");
  assert.equal(openInBusyKey("", "freecad"), "");
  assert.equal(openInBusyKey("/models/part.step", ""), "");
});

test("openInRequestUrl encodes both params", () => {
  assert.equal(
    openInRequestUrl("/models/my part.step", "system-default"),
    "/__cad/open-in?file=%2Fmodels%2Fmy%20part.step&app=system-default"
  );
});

test("fetchOpenInTargets returns [] on HTTP failure (older servers)", async () => {
  globalThis.fetch = async () => jsonResponse({}, { ok: false, status: 405 });
  assert.deepEqual(await fetchOpenInTargets(), []);
});

test("fetchOpenInTargets normalizes the server payload", async () => {
  globalThis.fetch = async () => jsonResponse({
    ok: true,
    targets: [{ id: "freecad", name: "FreeCAD", installed: true }],
  });
  assert.deepEqual(await fetchOpenInTargets(), [
    { id: "freecad", name: "FreeCAD", installed: true },
  ]);
});

test("requestOpenInApp posts and returns the payload", async () => {
  const requests = [];
  globalThis.fetch = async (url, options) => {
    requests.push({ url, options });
    return jsonResponse({ ok: true, appId: "freecad", appName: "FreeCAD", filename: "part.step" });
  };
  const payload = await requestOpenInApp({ file: "/models/part.step", app: "freecad" });
  assert.equal(payload.appName, "FreeCAD");
  assert.equal(requests.length, 1);
  assert.equal(requests[0].options.method, "POST");
  assert.equal(requests[0].url, "/__cad/open-in?file=%2Fmodels%2Fpart.step&app=freecad");
});

test("requestOpenInApp surfaces the server error message", async () => {
  globalThis.fetch = async () => jsonResponse({ ok: false, error: "FreeCAD is not installed" }, { ok: false, status: 400 });
  await assert.rejects(
    requestOpenInApp({ file: "/models/part.step", app: "freecad" }),
    /FreeCAD is not installed/
  );
});

test("requestOpenInApp validates its inputs before fetching", async () => {
  globalThis.fetch = async () => {
    throw new Error("should not fetch");
  };
  await assert.rejects(requestOpenInApp({ file: "", app: "freecad" }), /Missing STEP file/);
  await assert.rejects(requestOpenInApp({ file: "/models/part.step", app: "" }), /Missing Open-in app/);
});
