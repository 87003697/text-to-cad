// Resolve the URL of a sub-asset (the assembly.json descriptor or a component GLB) inside a
// component-GLB package, given the package's own asset URL.
//
// A package's catalog asset URL comes in one of two shapes:
//   - local-fs server:  /__cad/asset?file=<absolute package dir>&dir=<root>&v=<hash>
//   - blob/path server: https://<host>/<prefix>/<package dir>            (no query)
//
// In BOTH cases the descriptor and components live INSIDE the package directory. The naive
// `${packageUrl}/assembly.json` only works for the path-style URL — for the query-style URL it
// appends the sub-path after the query string, leaving `file=<dir>` pointing at the directory,
// which the asset server cannot serve (404 -> "is not a component-GLB package"). So we resolve
// the relative ref against the package dir explicitly and rewrite the `file` param.
//
// Resolving against the package dir is layout-agnostic: it handles the self-contained
// `components/<hash>.glb` refs and any legacy `../../components/<hash>.glb` refs alike.

const URL_RESOLUTION_BASE = "http://cad-viewer.local";

function isAbsoluteUrl(value) {
  return /^[a-z][a-z0-9+.-]*:\/\//iu.test(value);
}

// POSIX-resolve `relPath` against the absolute directory `baseDir`, collapsing "." and "..".
export function resolvePackageDirRef(baseDir, relPath) {
  const segments = String(baseDir).split("/");
  for (const part of String(relPath).split("/")) {
    if (part === "" || part === ".") {
      continue;
    }
    if (part === "..") {
      if (segments.length > 1) {
        segments.pop();
      }
      continue;
    }
    segments.push(part);
  }
  const joined = segments.join("/");
  return joined.startsWith("/") ? joined : `/${joined}`;
}

export function resolvePackageAssetUrl(packageAssetUrl, relPath) {
  const base = String(packageAssetUrl || "").trim();
  const ref = String(relPath || "").trim();
  if (!base || !ref) {
    return "";
  }
  const absolute = isAbsoluteUrl(base);
  const parsed = new URL(base, absolute ? undefined : URL_RESOLUTION_BASE);
  const packageDir = parsed.searchParams.get("file");
  if (packageDir) {
    // Query-style (local-fs) asset URL: repoint `file` at the resolved sub-asset path while
    // preserving `dir`/`v`. Appending to the query string would leave `file` at the directory.
    parsed.searchParams.set("file", resolvePackageDirRef(packageDir, ref));
    return absolute ? parsed.toString() : `${parsed.pathname}${parsed.search}`;
  }
  // Path-style (blob) asset URL: the package dir IS the URL path, so resolve the ref against
  // it directly. The trailing slash makes the package dir the base directory.
  return new URL(ref, base.endsWith("/") ? base : `${base}/`).toString();
}
