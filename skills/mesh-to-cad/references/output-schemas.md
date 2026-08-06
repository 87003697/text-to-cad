# Output schemas

Read this file when writing `route.json` (workflow step 2), choosing
artifact filenames (step 3), interpreting `compare_metrics.json`
thresholds (steps 4-5), writing `notes.md` (step 7), or writing
commit messages inside `${EXP_DIR}/`.

## File naming rules

All output files use underscores (never hyphens). Three naming classes
inside `${EXP_DIR}/`:

**Fixed names (stable, no iteration suffix)**

- `mesh_stats.json` — the `$mesh-inspect` numeric output, unmodified.
- `mesh_preview.png` — the `$mesh-inspect` multi-view preview render.
- `input_preview.glb` — sidecar `.glb` for `$cad-viewer` handoff, only
  when the source mesh is `.ply` or `.obj`.
- `route.json` — the routing decision (schema below).
- `compare_metrics.json` — the `$mesh-compare` numeric output for the
  latest iteration (each iter overwrites; use git history to retrieve
  prior iters). Its optional `voxblame` field contains only an overview and
  one next action.
- `voxblame/session.json`, `voxblame/reference.vbsvo`, and
  `voxblame/steps/<N>/{candidate.vbsvo,report.json}` — immutable hierarchical
  surface-grading snapshots and full reports. Each snapshot is complete, not
  a delta; `.tmp-*` directories are unpublished crash residue.
- `notes.md` — reconstruction notes (schema below).

**Basename-based (per-iter overwrite)**

- `<basename>.py` + `<basename>.step` + `<basename>.glb` (cad route),
  OR
- `<basename>.implicit.js` + `<basename>.glb` (implicit route).
- `<basename>` matches the input mesh identifier (e.g., `cup_cup_033`).
  The `.glb` is the mesh export consumed by `$mesh-compare`.

**Iter-suffix (`previews/` checkpoint PNGs)**

- `previews/heatmap_iter_<N>.png` — refine-iter diagnostic (kept per iter).
- `previews/side_by_side_iter_<final>.png` — accepted-iter visual verification.

Do NOT use variants like `mesh-stats.json`, `inspection.txt`,
`cad_fallback_*.png`, or hyphenated filenames. `input_preview.glb`
(mesh-inspect sidecar) and `<basename>.glb` (reconstruction export)
are distinct semantic files with distinct names — do not conflate them.

## `route.json` schema

```json
{
  "route": "cad" | "implicit-cad",
  "decision_reasons": ["λ1/λ2=8.2 suggests revolve axis", "watertight"],
  "mesh_features_used": ["pca_axes", "watertight", "euler_number"],
  "considered_alternative": {
    "route": "implicit-cad",
    "rejected_because": "clear revolve axis; parametric wins on fidelity"
  }
}
```

`considered_alternative` is REQUIRED even when the primary route is
obvious. Declaring the rejected option makes the decision auditable.

## `notes.md` seven sections (exact order)

1. `## Mesh Summary` — one sentence + key `mesh_stats` numbers
   (`face_count`, `is_watertight`, PCA ratio).
2. `## Route Decision` — one sentence citing `route.json`.
3. `## Modeling Operations` — bullet list of build123d operations used
   (extrude, revolve, loft, boolean, fillet, shell, pattern, assembly),
   or SDF equivalents for implicit route.
4. `## Preserved Structural Features` — enumerated features preserved:
   count-bearing details (multi-wing, multi-wheel, hole patterns),
   functional faces, load-bearing structures.
5. `## Omitted Surface Details` — enumerated features omitted with one
   reason each (fabric weave, wrinkles, decals, fine surface texture).
   No silent omissions.
6. `## Verification` — five fields:
   - `attempted: yes`
   - `succeeded: yes | no | partial`
   - `method: cad-viewer | snapshot | none`
   - `similarity_metrics: <path to compare_metrics.json>`
   - `similarity_render: <path to previews/side_by_side_iter_<final>.png | "not generated">`

   If the reconstruction loop exited via **plateau** or **divergence**
   (not accept), the section must also declare the residual metrics and
   the exit reason (e.g., "plateau at chamfer=0.018,
   max_directional_p95=0.041 after 3 iterations" or "iter 2 regressed
   vs iter 1; iter 1 kept as final").

7. `## Self-Assessed Quality` — score 0-10 + one-sentence rationale.

Any renamed, reordered, added, or omitted section violates the contract.

## Refinement thresholds

The full `compare_metrics.json` schema is authoritative in
`$mesh-compare`'s `references/compare-metrics.md`. Workflow step 4 must use
exactly `--samples 50000 --seed 0` in normalized Trellis2
`[-0.5, 0.5]^3` coordinates. A result measured with another sample count or
seed is diagnostic only and cannot receive an accept verdict.

**Accept** requires all three hard gates:

1. `chamfer ≤ 0.01`.
2. `stats.p95_a2b ≤ 0.03` AND `stats.p95_b2a ≤ 0.03`.
3. `voxblame.coarsest_first_error_depth` is null or greater than 3.

If any hard gate fails, the verdict is refine (subject to the existing
plateau/divergence exit rules). Consume the single `voxblame.next_action` when
non-null; otherwise fall back to `$mesh-compare` heatmap mode. Never embed the
full report's `current.errors` or `changes` arrays in
`compare_metrics.json`.

Across iterations, define `max_directional_p95` as the maximum of the two p95
fields. Meaningful progress is a 10% reduction in `chamfer`, a 10% reduction
in `max_directional_p95`, or a deeper
`voxblame.coarsest_first_error_depth` (null is best). This keeps a localized
tail improvement from being mislabeled as a plateau merely because the global
mean changed by less than 10%.

`hausdorff > 0.10` is a warning for a potentially severe isolated outlier;
record it in `notes.md § Verification` and use a heatmap when diagnosis is
needed, but it does not independently veto acceptance. Do not compute, append,
or use IoU for this workflow: voxel occupancy is resolution-dependent and is
unstable for thin structures such as wings.

These thresholds are the post-cup-pilot policy. Recalibrate only with a
recorded benchmark batch; do not change them inside an individual experiment.

## Git commit conventions

The experiment directory `${EXP_DIR}/` is an independent local git
repository (initialized by the pilot runner). Commit at the end of each
workflow phase:

- **Setup phase (one commit per step):**
  - `step 1: inspect (face_count=<N>, watertight=<bool>)`
  - `step 2: route to <cad|implicit-cad>`
- **Reconstruction loop (one commit per iter, at end of step 5):**
  - Accept: `iter <N>: chamfer=<X>, verdict=accept`
  - Refine: `iter <N>: chamfer=<X>, verdict=refine, diagnosis=<one-line>`
  - Plateau: `iter <N>: chamfer=<X>, verdict=plateau`
  - Divergence: save iter N's measured values, run `git checkout .` to discard
    its uncommitted modeling changes, then create an empty audit commit:
    `git commit --allow-empty -m "iter <N>: chamfer=<X>,
    verdict=plateau_via_divergence, kept=iter <N-1>"`. The kept final artifact
    and `compare_metrics.json` remain from iter N-1; the commit message and
    `notes.md § Verification` retain the discarded iteration's evidence.
- **Finalization phase (one commit per step):**
  - `step 6: verify (side-by-side rendered)`
  - `step 7: notes + verification (final chamfer=<X>)`

`git log --oneline` in `${EXP_DIR}/` yields the full iteration
trajectory without opening any JSON.
