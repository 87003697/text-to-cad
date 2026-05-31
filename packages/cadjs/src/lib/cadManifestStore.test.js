import assert from "node:assert/strict";
import test from "node:test";

import {
  cadViewerUsesHostedCatalog,
  readActiveCadDir
} from "./cadManifestStore.js";

function createMemorySessionStorage() {
  const store = new Map();
  return {
    getItem: (key) => store.get(key) ?? null,
    setItem: (key, value) => {
      store.set(key, String(value));
    },
    removeItem: (key) => {
      store.delete(key);
    },
  };
}

function withWindow(url, callback) {
  const previousWindow = globalThis.window;
  const sessionStorage = createMemorySessionStorage();
  globalThis.window = {
    location: { href: url },
    sessionStorage,
  };
  try {
    return callback({
      setHref(nextUrl) {
        globalThis.window.location.href = nextUrl;
      },
      sessionStorage,
    });
  } finally {
    if (previousWindow === undefined) {
      delete globalThis.window;
    } else {
      globalThis.window = previousWindow;
    }
  }
}

test("readActiveCadDir keeps directory mode when absolute file is inside the active dir", () => {
  withWindow("http://viewer.test/?dir=%2Ftmp%2Fmodels&file=%2Ftmp%2Fmodels%2Frobot.step", () => {
    assert.equal(readActiveCadDir(), "/tmp/models");
  });
});

test("readActiveCadDir enters file-only mode when absolute file is outside query dir", () => {
  withWindow("http://viewer.test/?dir=%2Ftmp%2Fmodels&file=%2Ftmp%2Fother%2Frobot.step", () => {
    assert.equal(readActiveCadDir(), "");
  });
});

test("readActiveCadDir enters file-only mode when absolute file is outside stored dir", () => {
  withWindow("http://viewer.test/?dir=%2Ftmp%2Fmodels&file=%2Ftmp%2Fmodels%2Frobot.step", ({ setHref }) => {
    assert.equal(readActiveCadDir(), "/tmp/models");

    setHref("http://viewer.test/?file=%2Ftmp%2Fother%2Frobot.step");
    assert.equal(readActiveCadDir(), "");

    setHref("http://viewer.test/?file=%2Ftmp%2Fmodels%2Fnext.step");
    assert.equal(readActiveCadDir(), "/tmp/models");
  });
});

test("readActiveCadDir keeps stored directory mode for relative file params", () => {
  withWindow("http://viewer.test/?dir=%2Ftmp%2Fmodels&file=%2Ftmp%2Fmodels%2Frobot.step", ({ setHref }) => {
    assert.equal(readActiveCadDir(), "/tmp/models");

    setHref("http://viewer.test/?file=robots%2Fnext.step");
    assert.equal(readActiveCadDir(), "/tmp/models");
  });
});

test("hosted catalog mode ignores local directory query state", () => {
  assert.equal(cadViewerUsesHostedCatalog("vercel-blob"), true);

  withWindow("http://viewer.test/?dir=%2Ftmp%2Fmodels&file=robots%2Fnext.step", ({ setHref }) => {
    assert.equal(readActiveCadDir({ assetBackend: "vercel-blob" }), "");

    setHref("http://viewer.test/?file=robots%2Fnext.step");
    assert.equal(readActiveCadDir({ assetBackend: "vercel-blob" }), "");
  });
});
