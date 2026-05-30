import { normalizeImplicitCadModel } from "./model.js";

const implicitCadModuleCache = new Map();

function cacheKey(url) {
  return String(url || "").trim();
}

function importUrl(url) {
  const rawUrl = String(url || "").trim();
  if (!rawUrl) {
    throw new Error("Missing implicit CAD module URL");
  }
  return rawUrl;
}

export async function loadImplicitCadModule(url, { signal } = {}) {
  const key = cacheKey(url);
  if (!key) {
    throw new Error("Missing implicit CAD module URL");
  }
  if (signal?.aborted) {
    throw new DOMException("The operation was aborted.", "AbortError");
  }
  if (!implicitCadModuleCache.has(key)) {
    let pending;
    pending = import(/* @vite-ignore */ importUrl(key)).then((moduleValue) => {
      const model = normalizeImplicitCadModel(moduleValue, { sourceUrl: key });
      if (implicitCadModuleCache.get(key) === pending) {
        implicitCadModuleCache.set(key, model);
      }
      return model;
    }).catch((error) => {
      implicitCadModuleCache.delete(key);
      throw error;
    });
    implicitCadModuleCache.set(
      key,
      pending
    );
  }
  const pending = implicitCadModuleCache.get(key);
  if (!pending || typeof pending.then !== "function") {
    return pending;
  }
  if (!signal) {
    return pending;
  }
  return new Promise((resolve, reject) => {
    const abort = () => {
      signal.removeEventListener?.("abort", abort);
      reject(new DOMException("The operation was aborted.", "AbortError"));
    };
    if (signal.aborted) {
      abort();
      return;
    }
    signal.addEventListener?.("abort", abort, { once: true });
    pending.then(
      (value) => {
        signal.removeEventListener?.("abort", abort);
        resolve(value);
      },
      (error) => {
        signal.removeEventListener?.("abort", abort);
        reject(error);
      }
    );
  });
}

export function peekImplicitCadModule(url) {
  const cached = implicitCadModuleCache.get(cacheKey(url));
  return cached && typeof cached.then !== "function" ? cached : null;
}
