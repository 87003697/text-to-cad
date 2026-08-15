/**
 * The Node half of `cadgen.coordination.require_write_lock`.
 *
 * A render package is only ever written from inside the artifact's generation lock, and for
 * the JS builders that lock is held by the PYTHON PARENT (`cadgen/_internal/node_runtime.py`)
 * -- Node cannot take it: `fs.flock` and `O_EXLOCK` do not exist. So the child cannot verify
 * the lock by taking it. What it can do is prove it was started by the holder:
 *
 * a run id reaches the sentinel ONLY from inside `exclusive()`, after `LOCK_EX` was taken
 * (`coordination/lock.py`), so a `--run-id` matching the sentinel's stamp is unforgeable
 * outside a held lock.
 *
 * That makes this a real boundary rather than a comment: a builder run by hand against a
 * package directory, or with a stale run id, throws before it writes a byte.
 *
 * It throws UNCONDITIONALLY -- there is no `CADGEN_STRICT_LOCKS` escape hatch like the Python
 * side's. The Python check is old enough to have callers whose environments must degrade
 * rather than fail; this one is new, and new code fails loud.
 */

import fs from "node:fs";
import path from "node:path";

/** Mirrors `coordination/paths.py` WRITE_LOCK_SUFFIX, which is declared FROZEN there. */
const WRITE_LOCK_SUFFIX = ".generation.lock";

/** Mirrors `coordination/lock.py` _RUN_ID_BYTES. */
const RUN_ID_BYTES = 32;

/** The hidden sibling sentinel a writer of `packageDir` holds. */
export function writeLockPath(packageDir) {
  const resolved = path.resolve(packageDir);
  return path.join(path.dirname(resolved), `.${path.basename(resolved)}${WRITE_LOCK_SUFFIX}`);
}

/**
 * Throw unless `runId` is the run id currently stamped into `packageDir`'s write sentinel.
 */
export function assertWriteLock(packageDir, runId) {
  const sentinel = writeLockPath(packageDir);
  const expected = String(runId || "").trim();
  let stamped = "";
  let readError = null;
  try {
    stamped = fs.readFileSync(sentinel).subarray(0, RUN_ID_BYTES).toString("ascii").trim();
  } catch (error) {
    // Say WHICH failure this is. Collapsing an unreadable sentinel into "" reported a
    // mismatch against a file that held exactly the right run id, which is how issue #269
    // presented: EBUSY from a mandatory Windows lock, described as a lock violation. The
    // lock no longer sits on this file, so a read error here means something else -- and
    // whatever it is, the reader deserves to be told about it rather than misdirected.
    readError = error;
  }
  if (readError) {
    throw new Error(
      `could not read the generation sentinel for ${packageDir}: `
      + `${readError.code || readError.message} reading ${sentinel}. `
      + "The run id could not be checked, so the package was not written."
    );
  }
  if (!expected || stamped !== expected) {
    throw new Error(
      `render package written without its generation lock: ${packageDir} `
      + `(--run-id ${expected || "<missing>"} does not match ${sentinel})`
    );
  }
}
