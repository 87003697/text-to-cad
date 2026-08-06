# BUGS.md — text-to-cad repo issues hit during the chronograph build

Running log of repo bugs, unexpected behavior, and doc gaps found while
building `models/one-shots/moonwatch/`. Watch-model problems do not belong
here. Format per entry: what I was doing, exact command, exact error/wrong
output, workaround, blocked?, fixed?

---

## 1. `packages/cadjs` ESM cannot be loaded standalone from a lightweight worktree

- **Doing:** extracting the `cinematic` theme preset JSON to author a
  presentation render theme (`node -e "import('./packages/cadjs/src/common/themeSettings.js')..."`).
- **Error:** `Cannot find package 'implicitjs' imported from packages/cadjs/src/common/camera.js`,
  then `Cannot find package 'three' imported from packages/implicitjs/src/common/camera.js`.
- **Cause:** worktrees are intentionally lightweight (no `node_modules`), and
  `cadjs` resolves `implicitjs`/`three` as bare specifiers, so even a module
  of pure data constants (`themeSettings.js`) cannot be imported without a
  full install.
- **Workaround:** symlinked `packages/cadjs/node_modules/implicitjs -> ../../implicitjs`
  and `packages/{cadjs,implicitjs}/node_modules/three -> <main checkout>/packages/cadjs/node_modules/three`.
- **Blocked:** no (workaround in minutes). **Fixed:** no (logged only —
  non-blocking; arguably by design).

## 2. `CADGEN_WARM=1`: killing the CLI client does not cancel the in-daemon job

- **Doing:** first build of `finishing_sampler.step.py` was slow (my own
  O(n^2) boolean accumulation); I killed the client
  (`pkill -f "scripts/gen finishing_sampler"`) and relaunched with fixed
  source.
- **Wrong output:** the relaunched client sat at 0% CPU for minutes. The
  daemon (pid from `$TMPDIR/cadgen-daemon-*.log`) was still burning ~600%
  CPU on the *killed* client's job — requests are handled sequentially, so
  the new run silently queued behind a job whose requester was gone.
- **Workaround:** `kill -9 <daemon pid>` (socket + staleness handling
  respawn a fresh daemon transparently on the next call).
- **Suggestion:** the daemon should abort a job when its client disconnects.
- **Blocked:** ~10 min lost. **Fixed:** no (workaround only).

## 3. Sub-mm finishing booleans: overlapping-tool networks are pathological (OCC, not a repo defect per se)

- **Doing:** perlage (overlapping 0.02 mm-deep spherical dimples) on a
  14×8 mm coupon for `models/one-shots/moonwatch/_finishing.py`.
- **Wrong output:** no error — `scripts/gen` sat in "Building geometry"
  indefinitely (>40 CPU-minutes for ~200 stamps; even ~60 stamps took
  minutes). Two escalating causes, both silent: (a) pairwise `a + b`
  accumulation of boolean tools is O(n²); (b) even in ONE multi-tool op,
  dimple spheres have ~15 mm radii, so every tool overlaps every other
  deep below the surface and OCC builds one giant intersection network.
- **Workaround (both applied):** batch all boolean tools into a single
  list-operand op, AND pre-clip each stamp to a small lens cap
  (`Sphere & Cylinder` prototype, translated copies) so tools are
  disjoint. 14×8 mm field: >40 CPU-min → 0.69 s.
- **Suggestion:** `scripts/gen` progress JSON could surface elapsed time
  per phase (it reports `ratio: 0.0` forever); a doc note in
  `references/build123d-modeling.md` about multi-tool list booleans would
  save others this cliff.
- **Blocked:** ~45 min lost. **Fixed:** in model helpers (no repo change).

## 4. `scripts/gen` prints nothing to stdout/stderr during long builds

- **Doing:** first `scripts/gen finishing_sampler.step.py` runs (issues 2/3).
- **Wrong output:** zero output for the entire run — no phase logging, no
  heartbeat; the only liveness signal is a hidden
  `__cadgen__/models/.<name>.generation.progress.json` (whose `ratio`
  stays 0.0 in the generate phase) plus `ps`. Made the hang look like a
  crash and cost several kill/retry cycles.
- **Workaround:** watch the progress JSON + process CPU by hand.
- **Blocked:** contributed to the ~45 min above. **Fixed:** no (logged).

## 5. Near-tangent boolean tools are dropped SILENTLY (OCC kernel via build123d)

- **Doing:** case cluster — flat crystal/crown/pusher domes built by intersecting
  huge near-tangent spheres (R≈1700 mm) with small revolves; also a crystal
  multi-tool subtract.
- **Wrong output:** no error, exit 0, `inspect validate` clean — but half a
  tool's material was silently not removed (pusher head half-vanished), and one
  subtract left a stray disjoint 21.6 mm³ solid floating inside the crystal.
  Classic silent-no-op/degenerate-geometry behavior at near-tangency; only
  visual snapshot review caught it.
- **Workaround:** avoid near-tangent booleans entirely — build such domes as a
  single revolved profile (RadiusArc in the profile), which is also crisper.
- **Blocked:** no (caught in builder self-review). **Fixed:** in model source.

## 6. Snapshot renderer shows transparent parts (alpha < 1 source colors) as milky-opaque

- **Doing:** case cluster snapshots; `crystal` has color alpha 0.16, sapphire
  0.14 (confirmed present in the artifact descriptor).
- **Wrong output:** in `scripts/snapshot` renders the crystal reads as a milky
  solid dome rather than glass; unclear whether the GLB bakes alpha and the
  snapshot material ignores it, or alpha is dropped earlier.
- **Workaround:** none yet; to be re-checked at whole-watch compose (may need
  `display.mode` tweaks or a transparent-materials fix).
- **Blocked:** not yet (cosmetic until final renders). **Fixed:** no.
- **Root cause (traced):** two independent alpha drops.
  1. `packages/cadgen/src/cadgen/_internal/glb.py add_material()` bakes the
     RGBA into `baseColorFactor` but never sets `alphaMode: "BLEND"`, and glTF
     defaults to OPAQUE → alpha ignored by conformant loaders. **Fixed in root
     source** (BLEND set when alpha < 1) — helps standalone GLB exports.
  2. The component-package compose path drops alpha entirely: descriptor
     occurrence override colors go through
     `packages/cadjs/src/lib/assembly/meshData.js linearRgbToHex()` (3
     channels only), and `lib/viewer/surfaceMaterials.js` derives opacity
     solely from theme/display-mode settings — there is no per-part opacity
     concept at all. A real fix means threading alpha through part records
     into per-material `transparent`/`opacity`; too invasive for this
     project's "minimal targeted fixes" rule.
- **Adopted workaround:** snapshot renders `--hide` the glass occurrences
  (crystal, caseback sapphire); optically defensible for macro shots.
