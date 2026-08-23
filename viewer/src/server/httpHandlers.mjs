import fs from "node:fs";
import path from "node:path";

import { openAssetHandleUnderRoot } from "./safeAssetOpen.mjs";

const STATIC_CONTENT_TYPES = new Map([
  [".css", "text/css; charset=utf-8"],
  [".html", "text/html; charset=utf-8"],
  [".ico", "image/x-icon"],
  [".js", "text/javascript; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
  [".map", "application/json; charset=utf-8"],
  [".svg", "image/svg+xml"],
  [".wasm", "application/wasm"],
]);

export function contentTypeForStaticAsset(filePath) {
  return STATIC_CONTENT_TYPES.get(path.extname(String(filePath || "")).toLowerCase()) || "";
}

export function sendJson(res, statusCode, payload, { cacheControl = "no-store" } = {}) {
  res.statusCode = statusCode;
  res.setHeader("content-type", "application/json; charset=utf-8");
  res.setHeader("cache-control", cacheControl || "no-store");
  res.end(JSON.stringify(payload));
}

function downloadFilename(value) {
  const rawFilename = path.basename(String(value || "").replace(/\\/g, "/")) || "download";
  return rawFilename.replace(/[\x00-\x1f"\\]/g, "_");
}

function encodeContentDispositionFilename(value) {
  return encodeURIComponent(value).replace(/['()*]/g, (char) => (
    `%${char.charCodeAt(0).toString(16).toUpperCase()}`
  ));
}

function attachmentContentDisposition(filename) {
  const safeFilename = downloadFilename(filename);
  const quotedFilename = safeFilename.replace(/[^\x20-\x7e]/g, "_");
  return `attachment; filename="${quotedFilename}"; filename*=UTF-8''${encodeContentDispositionFilename(safeFilename)}`;
}

function errorMessage(error) {
  return error instanceof Error ? error.message : String(error);
}

function requestRootDir(requestUrl) {
  return String(requestUrl?.searchParams?.get("dir") || "").trim();
}

function requestFileRef(requestUrl) {
  return String(requestUrl?.searchParams?.get("file") || "").trim();
}

function requestHeader(req, name) {
  const headers = req?.headers || {};
  const value = headers[String(name || "").toLowerCase()];
  return Array.isArray(value) ? value[0] : String(value || "");
}

function requestRefererUrl(req) {
  const value = requestHeader(req, "referer") || requestHeader(req, "referrer");
  if (!value) {
    return null;
  }
  try {
    return new URL(value, "http://localhost");
  } catch {
    return null;
  }
}

function siblingFileRef(sourceFileRef, relativeFileRef) {
  const source = String(sourceFileRef || "").replace(/\\/g, "/");
  const relative = String(relativeFileRef || "").replace(/\\/g, "/").replace(/^\/+/g, "");
  if (!source || !relative) {
    return "";
  }
  if (path.isAbsolute(source)) {
    return path.resolve(path.dirname(source), relative);
  }
  const sourceDir = path.posix.dirname(source);
  return path.posix.normalize(path.posix.join(sourceDir === "." ? "" : sourceDir, relative));
}

function legacyCadAssetFileRef(requestUrl, req) {
  if (!requestUrl.pathname.startsWith("/__cad/") || requestUrl.pathname === "/__cad/asset") {
    return "";
  }
  let relativePath = "";
  try {
    relativePath = decodeURIComponent(requestUrl.pathname.slice("/__cad/".length));
  } catch {
    return "";
  }
  if (!relativePath || !path.extname(relativePath)) {
    return "";
  }
  const refererUrl = requestRefererUrl(req);
  return siblingFileRef(requestFileRef(refererUrl), relativePath);
}

function readJsonBody(req, { limitBytes = 256 * 1024 } = {}) {
  return new Promise((resolve, reject) => {
    let body = "";
    req.setEncoding?.("utf8");
    req.on("data", (chunk) => {
      body += chunk;
      if (Buffer.byteLength(body) > limitBytes) {
        reject(new Error("Request body is too large"));
        req.destroy?.();
      }
    });
    req.on("error", reject);
    req.on("end", () => {
      const text = body.trim();
      if (!text) {
        resolve({});
        return;
      }
      try {
        resolve(JSON.parse(text));
      } catch {
        reject(new Error("Request body must be valid JSON"));
      }
    });
  });
}

function fileAssetRequest(backend, requestUrl, {
  rootDir,
  catalog,
} = {}) {
  const fileRef = requestFileRef(requestUrl);
  const request = {
    fileRef,
    asset: requestUrl.searchParams.get("asset") || "output",
    rootDir,
    catalog,
  };
  if (typeof backend.resolveRequestRoot === "function") {
    request.resolvedRoot = backend.resolveRequestRoot({ rootDir, fileRef });
  } else if (typeof backend.resolveRoot === "function" && rootDir) {
    request.resolvedRoot = backend.resolveRoot(rootDir);
  }
  return request;
}

function sendBufferDownload(res, {
  body,
  filename,
  contentType,
} = {}) {
  const bytes = Buffer.isBuffer(body) ? body : Buffer.from(body || "");
  res.statusCode = 200;
  res.setHeader("content-type", contentType || "application/octet-stream");
  res.setHeader("cache-control", "no-store");
  res.setHeader("content-disposition", attachmentContentDisposition(filename));
  res.setHeader("content-length", String(bytes.length));
  res.end(bytes);
}

export function serveStaticFile(filePath, req, res, next, { contentType, headers = {}, trustedRoot = null } = {}) {
  // Capability-style serving: when ``trustedRoot`` is threaded through
  // by the backend, open the file via the handle-based helper (see
  // ``viewer/src/server/safeAssetOpen.mjs``). The helper walks every
  // path segment, opens with ``O_NOFOLLOW`` where available, fstat-
  // validates the returned handle, and re-lstat-validates the ancestor
  // chain -- all BEFORE we accept the fd. We then stream from the
  // *already-open handle*, never by pathname. This closes the
  // check-vs-open race that ``fs.stat`` + ``fs.createReadStream(path)``
  // leaves open.
  //
  // When no trusted root is threaded (legacy callers such as the
  // static viewer bundle) we fall back to the lstat + O_NOFOLLOW
  // pattern for defense in depth.
  if (trustedRoot) {
    let handle;
    try {
      handle = openAssetHandleUnderRoot(trustedRoot, filePath);
    } catch (error) {
      if (error && Number(error.statusCode) === 403) {
        sendJson(res, 403, { error: "Forbidden" });
        return;
      }
      next();
      return;
    }
    if (res.destroyed) {
      fs.closeSync(handle.fd);
      return;
    }
    if (contentType) {
      res.setHeader("content-type", contentType);
    }
    for (const [name, value] of Object.entries(headers)) {
      if (value !== undefined && value !== null && value !== "") {
        res.setHeader(name, value);
      }
    }
    res.setHeader("cache-control", "no-store");
    res.setHeader("content-length", String(handle.size));
    // ``fd`` is what we stream from -- Node's ``createReadStream``
    // takes ownership and closes it when the stream ends.
    const stream = fs.createReadStream(null, { fd: handle.fd, autoClose: true });
    res.on("close", () => {
      if (!res.writableEnded) {
        stream.destroy();
      }
    });
    stream.on("error", () => {
      if (!res.headersSent) {
        next();
      } else {
        res.destroy();
      }
    });
    stream.pipe(res);
    return;
  }
  // Legacy path (no trusted root). ``fs.lstat`` refuses to follow the
  // link at the leaf, and on POSIX we open with ``O_NOFOLLOW`` so the
  // kernel rejects a symlink at the final component even if it
  // appeared between lstat and open. Ancestor swaps are NOT caught on
  // this path.
  fs.lstat(filePath, (lstatError, lstats) => {
    if (res.destroyed) {
      return;
    }
    if (lstatError || lstats.isSymbolicLink() || !lstats.isFile()) {
      next();
      return;
    }
    if (contentType) {
      res.setHeader("content-type", contentType);
    }
    for (const [name, value] of Object.entries(headers)) {
      if (value !== undefined && value !== null && value !== "") {
        res.setHeader(name, value);
      }
    }
    res.setHeader("cache-control", "no-store");
    res.setHeader("content-length", String(lstats.size));
    const openFlags = fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW ?? 0);
    const stream = fs.createReadStream(filePath, { flags: openFlags });
    res.on("close", () => {
      if (!res.writableEnded) {
        stream.destroy();
      }
    });
    stream.on("error", () => {
      if (!res.headersSent) {
        next();
      } else {
        res.destroy();
      }
    });
    stream.pipe(res);
  });
}

export function createCadViewerApiMiddleware({
  backend,
  serverInfo = () => ({}),
  enableStepArtifactBackend = false,
  claimDisabledStepArtifactRoute = false,
  preferFileDownloadRedirects = false,
  onCatalogChanged = () => {},
  onCatalogActivated = () => {},
  onDirectoryActivated = () => {},
  rootDir,
  catalogCacheControl = "",
} = {}) {
  if (!backend) {
    throw new Error("createCadViewerApiMiddleware requires backend");
  }
  return async function cadViewerApiMiddleware(req, res, next) {
    const requestUrl = new URL(req.url || "/", "http://localhost");
    const activeRootDir = requestRootDir(requestUrl) || rootDir || "";
    const activeFileRef = requestFileRef(requestUrl);
    if (requestUrl.pathname === "/__cad/server") {
      sendJson(res, 200, serverInfo({ rootDir: activeRootDir, fileRef: activeFileRef }));
      return;
    }
    if (requestUrl.pathname === "/__cad/directory/activate") {
      const method = String(req.method || "GET").toUpperCase();
      if (method !== "POST") {
        res.setHeader("allow", "POST");
        sendJson(res, 405, {
          error: "Use POST to activate a CAD Viewer directory",
        });
        return;
      }
      if (typeof backend.resolveRequestRoot !== "function" && typeof backend.resolveRoot !== "function") {
        sendJson(res, 501, {
          error: "Directory activation requires a local filesystem CAD Viewer backend",
        });
        return;
      }
      try {
        const resolvedRoot = typeof backend.resolveRequestRoot === "function"
          ? backend.resolveRequestRoot({ rootDir: activeRootDir, fileRef: activeFileRef })
          : backend.resolveRoot(activeRootDir);
        onDirectoryActivated(resolvedRoot, { rootDir: activeRootDir, fileRef: activeFileRef });
        sendJson(res, 200, {
          ok: true,
          directory: {
            dir: String(resolvedRoot?.dir || activeRootDir || ""),
            rootPath: String(resolvedRoot?.rootPath || ""),
            rootName: String(resolvedRoot?.rootName || ""),
          },
          server: serverInfo({ rootDir: String(resolvedRoot?.dir || activeRootDir || ""), fileRef: activeFileRef }),
        });
      } catch (error) {
        sendJson(res, 400, {
          ok: false,
          error: errorMessage(error),
        });
      }
      return;
    }
    if (requestUrl.pathname === "/__cad/catalog") {
      try {
        const catalog = await backend.readCatalog({ rootDir: activeRootDir, fileRef: activeFileRef });
        if (typeof backend.resolveRequestRoot === "function" && (activeRootDir || activeFileRef)) {
          onCatalogActivated(
            backend.resolveRequestRoot({ rootDir: activeRootDir, fileRef: activeFileRef }),
            { rootDir: activeRootDir, fileRef: activeFileRef },
          );
        } else if (activeRootDir && typeof backend.resolveRoot === "function") {
          onCatalogActivated(backend.resolveRoot(activeRootDir), { rootDir: activeRootDir, fileRef: activeFileRef });
        }
        sendJson(res, 200, catalog, { cacheControl: catalogCacheControl });
      } catch (error) {
        sendJson(res, 400, {
          error: errorMessage(error),
        });
      }
      return;
    }
    if (requestUrl.pathname === "/__cad/generation-status") {
      if (typeof backend.readGenerationStatus !== "function") {
        sendJson(res, 501, {
          error: "Generation status is not available for this CAD Viewer backend",
        });
        return;
      }
      try {
        sendJson(res, 200, await backend.readGenerationStatus({ rootDir: activeRootDir }));
      } catch (error) {
        sendJson(res, 400, {
          error: errorMessage(error),
        });
      }
      return;
    }
    if (requestUrl.pathname === "/__cad/download") {
      const method = String(req.method || "GET").toUpperCase();
      if (method !== "GET") {
        res.setHeader("allow", "GET");
        sendJson(res, 405, {
          error: "Use GET to download a file asset",
        });
        return;
      }

      try {
        const catalog = await backend.readCatalog({ rootDir: activeRootDir, fileRef: activeFileRef });
        const request = fileAssetRequest(backend, requestUrl, { rootDir: activeRootDir, catalog });

        if (preferFileDownloadRedirects && typeof backend.resolveFileAssetAccess === "function") {
          const access = await backend.resolveFileAssetAccess(request);
          if (access?.url) {
            res.statusCode = 302;
            res.setHeader("location", access.url);
            res.setHeader("cache-control", "no-store");
            res.end("");
            return;
          }
        }

        if (typeof backend.readFileAsset === "function") {
          const result = await backend.readFileAsset(request);
          sendBufferDownload(res, result);
          return;
        }

        if (typeof backend.resolveFileAssetAccess !== "function") {
          sendJson(res, 501, {
            error: "File downloads are not available for this CAD Viewer backend",
          });
          return;
        }

        const access = await backend.resolveFileAssetAccess(request);
        if (access?.path) {
          const resolvedRoot = typeof backend.resolveRequestRoot === "function"
            ? backend.resolveRequestRoot({ rootDir: activeRootDir, fileRef: activeFileRef })
            : null;
          serveStaticFile(access.path, req, res, () => {
            sendJson(res, 404, {
              error: "File asset not found",
            });
          }, {
            contentType: access.contentType || backend.contentTypeForPath?.(access.path) || "application/octet-stream",
            headers: {
              "content-disposition": attachmentContentDisposition(access.filename || access.file || access.path),
            },
            trustedRoot: resolvedRoot?.rootPath || null,
          });
          return;
        }
        if (access?.url) {
          res.statusCode = 302;
          res.setHeader("location", access.url);
          res.setHeader("cache-control", "no-store");
          res.end("");
          return;
        }
        sendJson(res, 404, {
          error: "File asset not found",
        });
      } catch (error) {
        sendJson(res, 400, {
          ok: false,
          error: errorMessage(error),
        });
      }
      return;
    }
    if (requestUrl.pathname === "/__cad/asset") {
      const method = String(req.method || "GET").toUpperCase();
      if (method !== "GET") {
        res.setHeader("allow", "GET");
        sendJson(res, 405, {
          error: "Use GET to read a CAD Viewer asset",
        });
        return;
      }
      try {
        if (typeof backend.assetPathForFileRef !== "function") {
          sendJson(res, 501, {
            error: "Direct CAD Viewer assets are not available for this backend",
          });
          return;
        }
        const resolvedRootForAsset = typeof backend.resolveRequestRoot === "function" && (activeRootDir || activeFileRef)
          ? backend.resolveRequestRoot({ rootDir: activeRootDir, fileRef: activeFileRef })
          : null;
        const assetPath = backend.assetPathForFileRef(activeFileRef, {
          rootDir: activeRootDir,
          ...(resolvedRootForAsset ? { resolvedRoot: resolvedRootForAsset } : {}),
        });
        if (!assetPath) {
          sendJson(res, 404, {
            error: "CAD Viewer asset not found",
          });
          return;
        }
        serveStaticFile(assetPath, req, res, () => {
          sendJson(res, 404, {
            error: "CAD Viewer asset not found",
          });
        }, {
          contentType: backend.contentTypeForPath?.(assetPath) || "application/octet-stream",
          trustedRoot: resolvedRootForAsset?.rootPath || null,
        });
      } catch (error) {
        if (Number(error?.statusCode) === 403) {
          sendJson(res, 403, {
            error: "Forbidden",
          });
          return;
        }
        sendJson(res, 400, {
          error: errorMessage(error),
        });
      }
      return;
    }
    if (requestUrl.pathname === "/__cad/reveal") {
      const method = String(req.method || "GET").toUpperCase();
      if (method !== "POST") {
        res.setHeader("allow", "POST");
        sendJson(res, 405, {
          error: "Use POST to reveal a file asset",
        });
        return;
      }

      try {
        if (typeof backend.openFileAsset !== "function") {
          sendJson(res, 405, {
            error: "Revealing files is only available for the local filesystem backend",
          });
          return;
        }
        const catalog = await backend.readCatalog({ rootDir: activeRootDir, fileRef: activeFileRef });
        const request = fileAssetRequest(backend, requestUrl, { rootDir: activeRootDir, catalog });
        const result = await backend.openFileAsset(request);
        sendJson(res, 200, {
          ok: true,
          ...result,
        });
      } catch (error) {
        sendJson(res, 400, {
          ok: false,
          error: errorMessage(error),
        });
      }
      return;
    }
    if (requestUrl.pathname === "/__cad/step-source-status") {
      if (typeof backend.readStepSourceStatus !== "function") {
        sendJson(res, 501, {
          error: "STEP source status is not available for this CAD Viewer backend",
        });
        return;
      }
      try {
        const catalog = await backend.readCatalog({ rootDir: activeRootDir, fileRef: activeFileRef });
        const request = {
          fileRef: activeFileRef,
          rootDir: activeRootDir,
          catalog,
        };
        if (typeof backend.resolveRequestRoot === "function") {
          request.resolvedRoot = backend.resolveRequestRoot({ rootDir: activeRootDir, fileRef: activeFileRef });
        } else if (typeof backend.resolveRoot === "function" && activeRootDir) {
          request.resolvedRoot = backend.resolveRoot(activeRootDir);
        }
        sendJson(res, 200, await backend.readStepSourceStatus(request));
      } catch (error) {
        sendJson(res, 400, {
          error: errorMessage(error),
        });
      }
      return;
    }
    if (requestUrl.pathname === "/__cad/step-artifact") {
      if (!enableStepArtifactBackend) {
        if (claimDisabledStepArtifactRoute) {
          sendJson(res, 501, {
            error: "STEP artifact generation is not enabled for this CAD Viewer backend",
          });
          return;
        }
        next();
        return;
      }
      if (req.method !== "POST") {
        sendJson(res, 405, {
          error: "Use POST to generate a STEP artifact",
        });
        return;
      }
      if (typeof backend.resolveRoot !== "function") {
        sendJson(res, 501, {
          error: "STEP artifact generation requires a local filesystem CAD Viewer backend",
        });
        return;
      }
      try {
        const catalog = await backend.readCatalog({ rootDir: activeRootDir, fileRef: activeFileRef });
        const resolvedRoot = typeof backend.resolveRequestRoot === "function"
          ? backend.resolveRequestRoot({ rootDir: activeRootDir, fileRef: activeFileRef })
          : backend.resolveRoot(activeRootDir);
        const result = await backend.generateStepArtifact({
          fileRef: activeFileRef,
          force: requestUrl.searchParams.get("force") === "1",
          resolvedRoot,
          catalog,
        });
        const nextCatalog = typeof backend.refreshCatalog === "function"
          ? await backend.refreshCatalog({ rootDir: activeRootDir, fileRef: activeFileRef })
          : await backend.readCatalog({ rootDir: activeRootDir, fileRef: activeFileRef });
        onCatalogChanged(resolvedRoot);
        sendJson(res, result.ok ? 200 : 500, {
          ok: result.ok,
          error: result.error,
          result: result.result,
          entry: backend.entryForSourcePath(nextCatalog, resolvedRoot, result.stepPath),
          catalog: nextCatalog,
        });
      } catch (error) {
        sendJson(res, 400, {
          error: errorMessage(error),
        });
      }
      return;
    }
    next();
  };
}

export function createLocalAssetMiddleware({ backend, rootDir } = {}) {
  if (!backend) {
    throw new Error("createLocalAssetMiddleware requires backend");
  }
  return function localAssetMiddleware(req, res, next) {
    const requestUrl = new URL(req.url || "/", "http://localhost");
    const fallbackFileRef = legacyCadAssetFileRef(requestUrl, req);
    if (
      (requestUrl.pathname !== "/__cad/asset" && !fallbackFileRef) ||
      typeof backend.assetPathForFileRef !== "function"
    ) {
      next();
      return;
    }
    let assetPath = null;
    let resolvedRoot = null;
    try {
      const refererUrl = requestRefererUrl(req);
      const activeRootDir = requestRootDir(requestUrl) || requestRootDir(refererUrl) || rootDir || "";
      const activeFileRef = requestFileRef(requestUrl) || fallbackFileRef;
      resolvedRoot = typeof backend.resolveRequestRoot === "function" && (activeRootDir || activeFileRef)
        ? backend.resolveRequestRoot({ rootDir: activeRootDir, fileRef: activeFileRef })
        : null;
      assetPath = backend.assetPathForFileRef(activeFileRef, {
        rootDir: activeRootDir,
        ...(resolvedRoot ? { resolvedRoot } : {}),
      });
    } catch (error) {
      if (Number(error?.statusCode) === 403) {
        res.statusCode = 403;
        res.end("Forbidden");
        return;
      }
      next();
      return;
    }
    if (!assetPath) {
      next();
      return;
    }
    // Thread the resolved trusted root through so ``serveStaticFile``
    // takes the capability-style handle path (safeAssetOpen). Without
    // it, this production middleware would fall back to the legacy
    // pathname-reopen branch, which does not catch ancestor-directory
    // swaps between resolver validation and stream open.
    serveStaticFile(assetPath, req, res, next, {
      contentType: backend.contentTypeForPath?.(assetPath) || undefined,
      trustedRoot: resolvedRoot?.rootPath || null,
    });
  };
}

// ``serveDistAsset`` serves the immutable built viewer bundle out of
// ``distRoot``. Its contents are shipped with the plugin and are NOT
// user-controlled -- there is no attacker write path to plant a
// symlink/junction inside ``distRoot``. This is the ONE remaining
// ``serveStaticFile`` call that stays on the legacy branch (no
// ``trustedRoot`` argument). All user-controlled asset serves --
// ``/__cad/download`` (line ~402), ``/__cad/asset`` API middleware
// (line ~462), and ``createLocalAssetMiddleware`` (line ~636) --
// thread the resolved trusted root through ``serveStaticFile`` so
// bytes come from the already-open, handle-validated fd (see
// ``viewer/src/server/safeAssetOpen.mjs``).
export function serveDistAsset({ distRoot, indexHtmlPath = path.join(distRoot, "index.html") } = {}) {
  return function distAssetMiddleware(req, res, next) {
    const requestUrl = new URL(req.url || "/", "http://localhost");
    const requestPath = requestUrl.pathname === "/" ? "/index.html" : requestUrl.pathname;
    let filePath = "";
    try {
      filePath = path.resolve(distRoot, decodeURIComponent(requestPath).replace(/^\/+/, ""));
    } catch {
      res.statusCode = 400;
      res.end("Bad request");
      return;
    }
    if (!(filePath === distRoot || filePath.startsWith(`${distRoot}${path.sep}`))) {
      res.statusCode = 403;
      res.end("Forbidden");
      return;
    }
    const fileExists = fs.existsSync(filePath);
    const isStaticAssetRequest = requestPath.startsWith("/assets/") || path.extname(requestPath);
    if (!fileExists && isStaticAssetRequest) {
      res.statusCode = 404;
      res.setHeader("content-type", "text/plain; charset=utf-8");
      res.setHeader("cache-control", "no-store");
      res.end("Not found");
      return;
    }
    const fallbackPath = fileExists ? filePath : indexHtmlPath;
    serveStaticFile(fallbackPath, req, res, next, {
      contentType: contentTypeForStaticAsset(fallbackPath) || undefined,
    });
  };
}
