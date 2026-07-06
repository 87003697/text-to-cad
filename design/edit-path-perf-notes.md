# Edit-path performance notes (deferred work)

Recorded 2026-07-06 after the round-3 pipeline work (`36c60638..13746038`).
Baselines measured on this branch: a comment edit in a closure file rebuilds
falcon_heavy in ~5.3 s (1.74 s via the `CADGEN_WARM` daemon) and tom in
~12.7 s (~8.6 s daemon), with every content-addressed component reused
(zero re-meshing). Profile split for falcon: ~71% interpreter+OCP import
tax, then gen_step compose, closure capture/hash, adaptive-hints rescan,
descriptor emit.

Ranked remaining wins (all adversarially verified against measurements):

1. **Use the warm daemon for builds** — shipped, opt-in (`CADGEN_WARM=1`).
   Verified for builds, not just skips: falcon 5.29 s → 1.74 s (−67%),
   tom 12.7 s → ~8.6 s. Consider defaulting it on for agent sessions.

2. **Comment-insensitive closure hashing** (`_internal/source_hash.py`) —
   hash compiled bytecode (or AST) per closure file instead of file bytes;
   every closure entry in both SpaceX models and tom is `.py`. Measured
   effect: a comment/whitespace edit becomes a ~47 ms warm skip instead of
   a full rebuild (falcon −67%, tom −86%; ~0.1–0.3 s combined with the
   daemon). Docstrings remain semantically visible to bytecode, which is
   the correct sensitivity. Record BOTH hashes during migration so old
   descriptors stay valid (byte-hash match OR bytecode-hash match).

3. **Correctness footgun (fix alongside #2):** stdin-launched builds
   (`python - <<EOF` driving cadgen APIs) make `_runtime_roots()` treat the
   entry's directory as runtime (via `__main__.__file__` handling), so an
   entry-only closure is recorded and staleness detection is silently
   disabled for that artifact. Surfaced during profiling; not yet fixed.

4. **sys.modules eviction/capture scan** (`evict_first_party_modules` +
   closure capture) — walks all of `sys.modules` with per-module pathlib
   work: measured 0.23–0.68 s per build (~30% of a daemon-path falcon
   build). Cache module-path classification by module name + prefix-match
   against the (already lru_cached) excluded roots.

5. **Adaptive-hints rescan** (`_scene_mesh_resolution_hints`) — 0.30 s per
   recompose even when every component cid is unchanged. Reuse the
   descriptor's recorded resolution when the occurrence cid set matches.

6. **tom's gen_step compose (6.4 s, ~50% of its rebuild)** — tom-specific:
   vendor STEP child loads are already binary-cached; the remaining cost is
   build123d compose. Adopting `cadgen.compound_from_instances` for its
   repeated servo/gripper parts recovers ~1–2 s (verifier deflated the
   original "several seconds" estimate — most of the 6.4 s is non-repeated
   part processing).

7. **Geometry-identical fast path** (~0.7 s on falcon: skip hints scan +
   provenance rebuild + package walk when all cids/transforms/names match
   the existing descriptor) — mostly subsumed by #2; only worth doing if a
   real geometry-identical-after-code-change workload shows up.

Not worth it (measured/refuted): per-edge adaptor caching for classification
(two orders below the estimate on generated models); descriptor re-parse
memoization (~0.1 s scale); eager scipy/sympy/IPython import trimming is
build123d-upstream, superseded by the daemon.
