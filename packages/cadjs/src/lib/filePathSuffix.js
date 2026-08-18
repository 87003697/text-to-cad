/**
 * Shortest unique path suffix (SUFP) — the compact way to say which file a ref belongs to.
 *
 * A ref copied out of the viewer should survive being pasted into a prompt that spans several
 * files, and `models/step/assemblies/motorcycle_shock_absorber.step.py#o1.1.2` is too long to
 * put in front of every ref. The shortest trailing run of path segments that names exactly one
 * entry is almost always just the filename.
 *
 * Keeping the extension is what makes that true. Across the repo's model tree, bare stems
 * collide for 71 of 315 names — `mounting_plate` exists as `.step.py`, `.stl`, `.3mf` and
 * `.glb` — while filename-with-extension collides for only 3 of 404 files. Format siblings are
 * the dominant collision and the extension is exactly what separates them.
 *
 * Emission is allowed to drift: adding a file that collides lengthens another entry's suffix.
 * Acceptance is not, which is why resolvers match any unambiguous suffix rather than only the
 * shortest one.
 */

/** Split a path into segments, tolerating Windows separators and duplicate slashes. */
function segmentsOf(path) {
  return String(path || "")
    .replace(/\\/g, "/")
    .split("/")
    .filter(Boolean);
}

/**
 * Map every path to its shortest unique trailing-segment run.
 *
 * Comparison is always segment-aligned: `late.stl` is NOT a suffix of `mounting_plate.stl`,
 * because suffix matching on raw strings would make refs resolve to the wrong file.
 */
export function shortestUniquePathSuffixes(paths) {
  const cleaned = [];
  const seen = new Set();
  for (const path of Array.isArray(paths) ? paths : []) {
    const normalized = segmentsOf(path).join("/");
    if (!normalized || seen.has(normalized)) {
      continue;
    }
    seen.add(normalized);
    cleaned.push(normalized);
  }

  // suffixCounts.get(k) tells how many paths end in a given k-segment run, so uniqueness is a
  // lookup rather than a rescan per candidate.
  const suffixCounts = new Map();
  const longest = cleaned.reduce((max, path) => Math.max(max, segmentsOf(path).length), 0);
  for (let k = 1; k <= longest; k += 1) {
    const counts = new Map();
    for (const path of cleaned) {
      const segments = segmentsOf(path);
      if (segments.length < k) {
        continue;
      }
      const candidate = segments.slice(segments.length - k).join("/");
      counts.set(candidate, (counts.get(candidate) || 0) + 1);
    }
    suffixCounts.set(k, counts);
  }

  const result = new Map();
  for (const path of cleaned) {
    const segments = segmentsOf(path);
    let suffix = path;
    for (let k = 1; k <= segments.length; k += 1) {
      const candidate = segments.slice(segments.length - k).join("/");
      if ((suffixCounts.get(k)?.get(candidate) || 0) === 1) {
        suffix = candidate;
        break;
      }
    }
    result.set(path, suffix);
  }
  return result;
}

/** The SUFP for one path within a known set, or the path itself when it is not in the set. */
export function shortestUniquePathSuffix(path, paths) {
  const normalized = segmentsOf(path).join("/");
  if (!normalized) {
    return "";
  }
  return shortestUniquePathSuffixes(paths).get(normalized) || normalized;
}

/**
 * Does `suffix` name `path`? Segment-aligned, so `plate.stl` matches `a/b/plate.stl` but
 * `late.stl` matches nothing.
 *
 * This is the same rule the CLIs apply when deciding whether a ref's file prefix refers to the
 * entry they were pointed at, and the rule an agent should use resolving a prefix to a file.
 */
export function pathHasSuffix(path, suffix) {
  const pathSegments = segmentsOf(path);
  const suffixSegments = segmentsOf(suffix);
  if (!suffixSegments.length || suffixSegments.length > pathSegments.length) {
    return false;
  }
  const tail = pathSegments.slice(pathSegments.length - suffixSegments.length);
  return tail.every((segment, index) => segment === suffixSegments[index]);
}
