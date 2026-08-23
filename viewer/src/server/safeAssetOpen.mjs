import fs from "node:fs";
import path from "node:path";

// Test-only global pre-open hook. Middleware/integration tests install
// a hook via ``__setSafeOpenPreOpenHookForTests`` to interpose a
// filesystem mutation deterministically between the segment walk and
// the ``open`` syscall so the race window can be exercised through the
// full production HTTP path -- ``serveStaticFile`` does not otherwise
// expose the hook. Production callers never install a hook.
let __testPreOpenHook = null;
export function __setSafeOpenPreOpenHookForTests(hook) {
  __testPreOpenHook = typeof hook === "function" ? hook : null;
}

// Handle-based safe open under a trusted root. Reviewer contract:
//   * Every path segment from the trusted root down to the leaf is
//     inspected with ``lstat`` and rejected if it is a symbolic link
//     (Node reports Windows junctions and reparse points as symbolic
//     links here, so the same walk covers both platforms).
//   * The leaf is opened via ``fs.openSync`` with ``O_NOFOLLOW`` where
//     available. The returned handle is validated via ``fstat`` and
//     compared against the pre-open ``lstat`` (dev + ino) so an
//     attacker who replaces the leaf OR any ancestor between the walk
//     and the open is caught -- ``open()`` would then bind to a
//     different inode than the one the walk saw, and the mismatch is
//     rejected before we return the handle.
//   * The returned ``{fd, size}`` is what the caller streams from.
//     Callers MUST NOT reopen the file by pathname; the whole point of
//     this helper is that the trusted state is the OPEN handle.
//
// ``preOpenHook`` is a test-only seam that fires AFTER the segment
// walk and BEFORE the ``open`` call, letting a test deterministically
// interpose a filesystem mutation to exercise the ``open``-vs-check
// race window. Production callers never pass this option.
export function openAssetHandleUnderRoot(trustedRoot, filePath, { preOpenHook = null } = {}) {
  if (!trustedRoot) {
    throw makeError("trusted root is required", 500);
  }
  if (!filePath) {
    throw makeError("file path is required", 400);
  }
  const rootAbs = path.resolve(trustedRoot);
  const targetAbs = path.resolve(filePath);
  // Lexical containment first (cheap early-out that also rejects
  // ``..`` traversal from the request string).
  const lexRel = path.relative(rootAbs, targetAbs);
  const firstLex = lexRel.split(/[\\/]/, 1)[0];
  if (lexRel === "" || firstLex === ".." || path.isAbsolute(lexRel)) {
    throw makeError(`asset ${filePath} is outside the trusted root`, 403);
  }

  const segments = lexRel.split(/[\\/]/).filter(Boolean);
  if (segments.length === 0) {
    throw makeError(`asset ${filePath} refers to the trusted root itself`, 403);
  }

  // Segment walk: lstat each cumulative ancestor and require it is
  // (a) not a symbolic link (Node reports junctions here too) and
  // (b) either a directory (for intermediates) or a regular file
  //     (for the leaf). Identity ({dev, ino}) is captured as BigInt
  // via ``{ bigint: true }`` -- Windows especially can produce 64-bit
  // inode identifiers that collide after Number truncation past
  // ``2^53``. Numeric equality would silently accept an attacker
  // whose planted inode differs only above the safe integer range.
  const walkStats = [];
  let cursor = rootAbs;
  const rootLstat = safeLstatBigInt(cursor);
  if (!rootLstat) {
    throw makeError(`trusted root ${cursor} is missing`, 500);
  }
  if (rootLstat.isSymbolicLink()) {
    throw makeError(`trusted root ${cursor} is a symbolic link; refusing to serve`, 500);
  }
  walkStats.push({ path: cursor, dev: rootLstat.dev, ino: rootLstat.ino });
  for (let i = 0; i < segments.length; i += 1) {
    cursor = path.join(cursor, segments[i]);
    const lst = safeLstatBigInt(cursor);
    if (!lst) {
      throw makeError(`asset segment ${cursor} is missing`, 404);
    }
    if (lst.isSymbolicLink()) {
      throw makeError(`asset segment ${cursor} is a symbolic link; refusing to follow`, 403);
    }
    const isLeaf = i === segments.length - 1;
    if (isLeaf) {
      if (!lst.isFile()) {
        throw makeError(`asset ${cursor} is not a regular file`, 403);
      }
    } else if (!lst.isDirectory()) {
      throw makeError(`asset ancestor ${cursor} is not a directory`, 403);
    }
    walkStats.push({ path: cursor, dev: lst.dev, ino: lst.ino });
  }

  // Test seam: fire the pre-open hook so the test can perform a
  // deterministic filesystem swap between the walk and the open. The
  // real security invariant below is that the open handle's ino/dev
  // must still match what we recorded, regardless of any interposed
  // mutation. Prefer the caller-supplied hook (unit tests) over the
  // global one (middleware integration tests).
  const effectiveHook = typeof preOpenHook === "function" ? preOpenHook : __testPreOpenHook;
  if (effectiveHook) {
    effectiveHook({ resolvedPath: targetAbs });
  }

  // Open the leaf. ``O_NOFOLLOW`` on POSIX makes the kernel refuse a
  // final-segment symbolic link so the leaf-swap race is closed at
  // the syscall boundary. On Windows ``O_NOFOLLOW`` is 0 (not
  // defined), but the following fstat-vs-lstat consistency check
  // catches an ancestor OR leaf swap even without it.
  const O_NOFOLLOW = fs.constants.O_NOFOLLOW ?? 0;
  let fd;
  try {
    fd = fs.openSync(targetAbs, fs.constants.O_RDONLY | O_NOFOLLOW);
  } catch (error) {
    if (error && (error.code === "ELOOP" || error.code === "ENOENT")) {
      throw makeError(`asset ${targetAbs} vanished or became a symbolic link before open`, 404);
    }
    throw error;
  }
  try {
    const fst = fs.fstatSync(fd, { bigint: true });
    if (!fst.isFile()) {
      throw makeError(`asset ${targetAbs} is not a regular file`, 403);
    }
    // Consistency check: the open handle MUST refer to the same
    // inode/device pair the segment walk recorded for the leaf. If
    // any ancestor was swapped between the walk and the open, or if
    // the leaf was swapped despite our O_NOFOLLOW request, fstat will
    // reveal a different inode than the walk saw and we reject.
    // BigInt comparison is exact for the full 64-bit range.
    const walkLeaf = walkStats[walkStats.length - 1];
    if (walkLeaf.ino !== fst.ino || walkLeaf.dev !== fst.dev) {
      throw makeError(
        `asset ${targetAbs} was replaced between the containment check and the open`,
        403,
      );
    }
    // Re-lstat every ancestor and require dev/ino unchanged. This
    // catches an ancestor-directory swap that would keep the LEAF's
    // inode but redirect a future path-based reopen; even though we
    // never reopen by pathname, the ancestor swap indicates a
    // successful TOCTOU attempt and we fail closed.
    for (let i = 0; i < walkStats.length; i += 1) {
      const entry = walkStats[i];
      const lst = safeLstatBigInt(entry.path);
      if (!lst) {
        throw makeError(`asset ancestor ${entry.path} vanished during open`, 404);
      }
      if (lst.isSymbolicLink()) {
        throw makeError(`asset ancestor ${entry.path} was replaced with a symbolic link during open`, 403);
      }
      if (lst.ino !== entry.ino || lst.dev !== entry.dev) {
        throw makeError(`asset ancestor ${entry.path} was replaced during open`, 403);
      }
    }
    // ``size`` is safely Number-truncated on the way out because HTTP
    // Content-Length is bounded well within ``Number.MAX_SAFE_INTEGER``
    // for any realistic asset. Identity comparisons above have
    // already been done in BigInt space.
    return { fd, size: Number(fst.size), mtimeMs: Number(fst.mtimeMs), path: targetAbs };
  } catch (error) {
    fs.closeSync(fd);
    throw error;
  }
}

function safeLstatBigInt(p) {
  try {
    return fs.lstatSync(p, { bigint: true });
  } catch {
    return null;
  }
}

function makeError(message, statusCode) {
  const error = new Error(message);
  error.statusCode = statusCode;
  return error;
}
