---
name: mesh-to-cad
description: Reconstruct parametric or implicit CAD from a 3D mesh file.
---

# Mesh-to-CAD reconstruction

## Purpose

Reconstruct a parametric or implicit CAD model from a 3D mesh file.
Route the mesh to `$cad` or `$implicit-cad` based on measured mesh
statistics; see `references/routing-rubric.md` for the rubric.

## Use this skill when

Use this skill when the user provides a 3D mesh file (`.ply`, `.obj`,
`.stl`, `.glb`) and asks for a parametric CAD or implicit CAD
reconstruction.

## Default assumptions

Use these defaults unless the user specifies otherwise:

- Units: millimeters (or as declared by the source dataset).
- Coordinate frame: PCA-based canonical frame, principal axis along +Z.
- Output directory: the `EXP_DIR` path handed by the caller; all
  artifacts live inside it.

## Tools and paths

This skill is a pure orchestrator; it does not ship its own CLI. Each
workflow step delegates to a peer skill: `$mesh-inspect` for mesh
statistics, `$cad` or `$implicit-cad` for modeling, `$mesh-compare`
for similarity measurement and multi-view rendering, and
(optionally) `$cad-viewer` for interactive review. See § Required
workflow for the concrete step-to-skill mapping.

## Required workflow

The iteration counter `N` starts at 0 and increments each time step 5
loops back to step 3. Loop exit is **metric-driven**, not count-based:
the loop stops when metrics either meet thresholds, plateau, or
regress. Scale depth to the task — a simple mesh may accept at N=0;
a complex assembly may converge over several refinements.

### Setup phase (once per experiment)

1. **Inspect the mesh.** Invoke `$mesh-inspect` on the input mesh. The
   skill will produce `${EXP_DIR}/mesh_stats.json` (used for routing),
   `${EXP_DIR}/mesh_preview.png` (multi-view input preview), and for
   `.ply` / `.obj` inputs `${EXP_DIR}/input_preview.glb` (sidecar for
   `$cad-viewer`). Optionally returns a `$cad-viewer` link. Keep all
   handoff artifacts alongside the stats for later reference.
2. **Route by rubric.** Apply the rubric in `references/routing-rubric.md`
   against the mesh stats; write `route.json` per the schema in
   `references/output-schemas.md`, including the rejected alternative.

### Reconstruction loop (iter N from 0; metric-driven exit)

3. **Model or remodel.** Use `$cad` or `$implicit-cad` SKILL.md as the
   authoritative modeling contract. Save primary artifacts to
   `${EXP_DIR}/` with basename matching the input mesh id:
   `<basename>.py` + `<basename>.step` + `<basename>.glb` (cad route)
   OR `<basename>.implicit.js` + `<basename>.glb` (implicit route). The
   `.glb` is the mesh export consumed by step 4.
4. **Measure similarity (numeric).** Run `$mesh-compare` (numeric CLI)
   with the input mesh and the reconstructed `<basename>.glb` (produced
   at step 3) → `${EXP_DIR}/compare_metrics.json`. Interpret against
   thresholds in `references/output-schemas.md`.
5. **Decide next action and commit (or discard) the iter.** Read
   `compare_metrics.json` (iter N, on disk) and, for N ≥ 1, iter N-1's
   metrics via `git show HEAD:compare_metrics.json` (HEAD is iter N-1's
   commit; iter N has not been committed yet). Apply exit logic:

   - **Accept**: metrics within thresholds (chamfer_l2 and iou both
     within `references/output-schemas.md` thresholds) →
     `git add . && git commit -m "iter <N>: chamfer=<X>, verdict=accept"`;
     iter N is `<final>`; exit loop to step 6.
   - **Continue refine**: metrics exceed thresholds AND iter N
     chamfer_l2 dropped by ≥ 10% vs iter N-1 (or N=0, no baseline) →
     invoke `$mesh-compare` **heatmap** mode →
     `${EXP_DIR}/previews/heatmap_iter_<N>.png`. `git add . && git
     commit -m "iter <N>: chamfer=<X>, verdict=refine, diagnosis=<one-line>"`.
     Use the JSON magnitude to prioritize the largest error mode and
     the heatmap to locate which region diverged; adjust the modeling
     source accordingly, increment N, loop back to step 3.
   - **Plateau stop**: metrics exceed thresholds BUT chamfer_l2
     dropped less than 10% vs iter N-1 → `git add . && git commit -m
     "iter <N>: chamfer=<X>, verdict=plateau"`; iter N is `<final>`;
     declare "converged below threshold; further refinement not
     improving" in step 7's `notes.md § Verification`; exit loop.
   - **Divergence stop**: iter N chamfer_l2 WORSE than iter N-1 →
     `git checkout .` to discard iter N's uncommitted changes; iter
     N-1 (current HEAD) is `<final>`; declare "iter N regressed from
     iter N-1" in step 7's `notes.md § Verification`; exit loop.

### Finalization phase (once, on loop exit)

6. **Verify visually.** Invoke `$mesh-compare` **side-by-side** mode →
   `${EXP_DIR}/previews/side_by_side_iter_<final>.png` where `<final>`
   is the accepted iteration number from step 5. Optionally hand off
   to `$cad-viewer` for interactive inspection of the reconstructed
   CAD. See § Handoff.
7. **Write `notes.md`** per the schema in `references/output-schemas.md`
   (seven sections, fixed order), including final `compare_metrics.json`
   values and any unresolved divergence.

## Handoff

Include the following in the final response:

- **Structured summary**: `notes.md` path — canonical write-up of
  routing, modeling operations, preserved/omitted features, and
  verification.
- **Primary artifact**: the reconstructed `.step` / `.stp` (cad route)
  or `.implicit.js` (implicit route) path.
- **Objective verification**: `compare_metrics.json` path AND the key
  metric values (chamfer_l2, hausdorff, iou) inline.
- **Visual verification** (authoritative): `${EXP_DIR}/previews/side_by_side_iter_<final>.png`
  path — the multi-view side-by-side rendered at the accepted iteration.
- **Routing transparency**: `route.json` path.
- **Iteration trajectory**: `${EXP_DIR}/previews/` (all
  `heatmap_iter_<N>.png` refine diagnoses) + `git log --oneline` in
  `${EXP_DIR}/` for full iteration history.
- **Interactive 3D** (optional): hand the reconstructed
  `<basename>.step` / `<basename>.stp` (cad route) or
  `<basename>.implicit.js` (implicit route) file path to `$cad-viewer`
  when that skill is installed; include the live viewer link(s). Skip
  cleanly if unavailable — the multi-view PNG is sufficient
  verification.

If any artifact is unavailable (loop exited via plateau / divergence,
`$cad-viewer` not installed, etc.), explicitly note which and why.

## Non-negotiables

- Follow all schemas in `references/output-schemas.md` verbatim: file naming, `route.json` (including `considered_alternative`), `notes.md` seven sections, and refinement thresholds. `compare_metrics.json` schema itself is defined in `$mesh-compare`'s `references/compare-metrics.md`.
- Preserve structural features by count and class (repeated elements > 3, multi-wing/-wheel/-leg, hole patterns). Any omission or agent-initiated simplification must be declared in `## Omitted Surface Details`.
- Run the measure step (workflow step 4) and record `compare_metrics.json`; do not invent numbers. If the loop exits via plateau or divergence (not accept), declare the reason and residual metrics in `notes.md § Verification`.
- Commit at the end of each workflow phase in `${EXP_DIR}/`: one commit per Setup step (1, 2), one commit per reconstruction iteration (steps 3-5 together, at end of step 5's decision), one commit per Finalization step (6, 7). Divergence stop discards iter changes via `git checkout .` without committing. Commit message format per `references/output-schemas.md § Git commit conventions`.
- Report only checks that actually ran or are directly supported by tool output.

## Progressive references

Load these files or peer skills only when their trigger applies:

- `references/routing-rubric.md` — routing decision table with thresholds
  (PCA λ1/λ2, face count, Euler characteristic, organic scan whitelist);
  trigger: workflow step 2.
- `references/output-schemas.md` — `route.json` schema, `notes.md`
  seven-section spec, file naming rules, and `compare_metrics.json`
  thresholds; trigger: workflow steps 2, 3, 4, 5, 7.
- `$mesh-inspect` — mesh statistics computation; trigger: workflow
  step 1.
- `$mesh-compare` — similarity metrics (numeric) and multi-view render
  (visual mode); trigger: workflow steps 4, 5, 6.
- `$cad` / `$implicit-cad` — modeling contracts for the routed skill;
  trigger: workflow step 3.
- `$cad-viewer` — optional interactive review of the reconstructed
  CAD; trigger: workflow step 6.
