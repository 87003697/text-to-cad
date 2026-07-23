# Routing rubric

Read this file when applying Required workflow step 2 to decide between
the `$cad` and `$implicit-cad` skills based on mesh statistics.

Apply these judgments in order; the first match wins:

1. **PCA λ1/λ2 > 4 with clear axial symmetry** → **`$cad` (revolve pattern)**.
   Elongated shapes with a dominant axis are best served by parametric
   revolves. Confirm the mesh has rotational symmetry around the primary
   axis before choosing this route.

2. **Face count > 100k, non-manifold, or Euler characteristic != 2** →
   **`$implicit-cad`**. The mesh is not clean enough for OCC boolean
   operations; SDF representation is more robust.

3. **Organic scan (dog / lion / human / animal / creature / plant)** →
   **`$implicit-cad`** (whitelist enforced). SDF handles organic
   surfaces where parametric CAD would produce artificial creases.

4. **Watertight + clearly machinable features (holes, fillets, bosses,
   ribs)** → **`$cad`**. Machinable geometry benefits from parametric
   control.

5. **Otherwise → agent judgment.** Document the chosen route AND the
   rejected alternative in `route.json.considered_alternative` with a
   one-sentence rationale.

## Boundary cases

- **Symmetric organic** (e.g., simple animal figure with clear rotational
  symmetry): prefer `$cad` if features are ≤ 5; else `$implicit-cad`.
- **Watertight but very high face count** (> 200k, dense scan): prefer
  `$implicit-cad` — the topology suggests scan data, not CAD-original.

## Fields read from `mesh_stats.json`

- `canonical_frame.eigenvalues` — three PCA eigenvalues (sorted
  descending). Compute λ1/λ2 as `eigenvalues[0] / eigenvalues[1]`.
- `stats.faces` — face count.
- `quality.euler_number` — Euler characteristic.
- `quality.watertight` — boolean.
- `quality.degenerate_faces` — non-zero suggests non-manifold input.
- Object category from the file basename (e.g. `chair_*.ply`,
  `dog_*.ply`) — used for the organic whitelist match; `mesh-inspect`
  does not surface a category field.
