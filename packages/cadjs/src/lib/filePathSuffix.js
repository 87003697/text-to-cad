/**
 * Shortest unique path suffix (SUFP) — the compact way to say which file a ref belongs to.
 *
 * A ref copied out of the viewer should survive being pasted into a prompt that spans several
 * files, and `models/step/assemblies/motorcycle_shock_absorber.step.py#o1.1.2` is too long to
 * put in front of every ref. The shortest trailing run of path segments that names exactly one
 * entry is almost always just the filename.
 *
 * Keeping the format extension is what makes that true. Across the repo's model tree, bare
 * stems collide for 71 of 315 names — `mounting_plate` exists as `.step.py`, `.stl`, `.3mf`
 * and `.glb` — while the displayed filename collides for only 3 of 415. Format siblings are
 * the dominant collision and the extension is exactly what separates them.
 *
 * `.step` itself is dropped, because STEP is the default subject of the whole tool and the
 * word earns nothing in a ref: `bracket.step.py` shows as `bracket.py`, raw `bracket.step` as
 * `bracket`. That costs no uniqueness — both schemes leave the same three filenames colliding
 * — but it does mean a displayed name is NOT a literal path suffix, so resolving one back to a
 * file means expanding it (see refDisplayNameCandidates), not matching it verbatim.
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

// `.step` carries no information in a ref -- STEP is the default subject of the whole tool -- so
// it is dropped from the displayed name: `bracket.step.py` shows as `bracket.py`, and a raw
// `bracket.step` shows as `bracket`. What remains still separates the format families, which is
// the job the extension was kept for: on this repo's tree both schemes leave exactly the same
// three filenames colliding, so this costs nothing in uniqueness.
const STEP_DISPLAY_SUFFIXES = [
  [".step.py", ".py"],
  [".stp.py", ".py"],
  [".step", ""],
  [".stp", ""]
];

/** The name a ref shows for this file: `bracket.step.py` -> `bracket.py`, `x.step` -> `x`. */
export function refDisplayName(fileName) {
  const name = String(fileName || "").trim();
  for (const [suffix, replacement] of STEP_DISPLAY_SUFFIXES) {
    if (name.toLowerCase().endsWith(suffix)) {
      return name.slice(0, name.length - suffix.length) + replacement;
    }
  }
  return name;
}

/** A path with its final segment reduced to the displayed name. */
function displayPath(path) {
  const segments = segmentsOf(path);
  if (!segments.length) {
    return "";
  }
  segments[segments.length - 1] = refDisplayName(segments[segments.length - 1]);
  return segments.join("/");
}

/**
 * Every real filename a displayed name could have come from — the inverse of refDisplayName.
 *
 * `bracket.py` may be `bracket.step.py` or a literal `bracket.py`; a bare `bracket` may be
 * `bracket.step`, `bracket.stp`, or literally `bracket`. Resolution has to try all of them,
 * which is why the skill docs tell an agent to expand rather than match the prefix literally.
 */
export function refDisplayNameCandidates(displayName) {
  const name = String(displayName || "").trim();
  if (!name) {
    return [];
  }
  const candidates = [name];
  if (name.toLowerCase().endsWith(".py")) {
    const stem = name.slice(0, name.length - ".py".length);
    candidates.push(`${stem}.step.py`, `${stem}.stp.py`);
  } else if (!name.includes(".")) {
    candidates.push(`${name}.step`, `${name}.stp`);
  }
  return candidates;
}

/**
 * Map every path to its shortest unique trailing-segment run.
 *
 * Comparison is always segment-aligned: `late.stl` is NOT a suffix of `mounting_plate.stl`,
 * because suffix matching on raw strings would make refs resolve to the wrong file.
 */
export function shortestUniquePathSuffixes(paths) {
  // Keyed by the ORIGINAL path, computed over the DISPLAY form: the suffix a user sees and
  // pastes is the thing that has to be unique.
  const originalByDisplay = new Map();
  const cleaned = [];
  const seen = new Set();
  for (const path of Array.isArray(paths) ? paths : []) {
    const normalized = segmentsOf(path).join("/");
    if (!normalized || seen.has(normalized)) {
      continue;
    }
    seen.add(normalized);
    const shown = displayPath(normalized);
    cleaned.push(shown);
    if (!originalByDisplay.has(shown)) {
      originalByDisplay.set(shown, normalized);
    }
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
    result.set(originalByDisplay.get(path) || path, suffix);
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
  const pathSegments = segmentsOf(displayPath(path));
  const suffixSegments = segmentsOf(displayPath(suffix));
  if (!suffixSegments.length || suffixSegments.length > pathSegments.length) {
    return false;
  }
  const tail = pathSegments.slice(pathSegments.length - suffixSegments.length);
  return tail.every((segment, index) => segment === suffixSegments[index]);
}
