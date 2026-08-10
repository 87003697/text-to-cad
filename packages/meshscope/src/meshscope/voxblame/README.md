# VoxBlame canonical surface evidence

VoxBlame is the framework-agnostic geometry engine for the canonical repair
Workspace. It compares candidates already expressed in one immutable Canonical
Reference frame and publishes conservative surface-occupancy evidence. It does
not choose CAD edits, issue repair verdicts, control Workspace state, or select
the final result.

## Pipeline

```text
raw evaluated scene
  → one Canonical Reference preparation
  → canonical candidate
  → depth-1–8 interior occupancy + Exterior Surface snapshot
  → exact depth-8 missing/excess evidence
  → complete Repair Target partition
  → Measured Step report and summary
  → optional Region Diff / formal preview / rebuild verification
```

## Modules

| Module | Responsibility |
|---|---|
| `prepare_reference.py` | Evaluated-scene capture, one-time normalization, identities, validation, atomic publication. |
| `canonical_artifacts.py` | Fail-closed Canonical Reference and candidate readers. |
| `frame.py` | Fixed canonical-lattice transform and finite triangle validation. |
| `tree.py` | Immutable indexed surface tree, traversal, identities, and Morton coordinate decoding. |
| `codec.py` | Strict `.vbsvo` encoding, decoding, and atomic file I/O. |
| `voxelize.py` / `_native.cpp` | Conservative Python/native triangle-AABB surface occupancy. |
| `exterior.py` | Canonical-cube clipping, exact exterior facts, signed diagnostic occupancy, and identity. |
| `targets.py` | Complete deterministic Repair Target partition, masks, identities, and paging. |
| `measurement.py` | Depth-1–8 measurement and atomic Measured Step evidence publication. |
| `region_diff.py` | Closed Repair Batch validation and objective fixed-region evidence. |
| `preview.py` | Formal residual channels, identity validation, and atomic preview publication. |
| `verification.py` | Non-publishing rebuilt Observable Geometry comparison. |
| `contracts.py` | Closed validators for session, report, and summary documents. |

The exhaustive schemas and cross-document invariants are in
[CONTRACT.md](CONTRACT.md).

## Public Python boundary

```python
from meshscope.voxblame import (
    measure_step,
    page_repair_targets,
    prepare_reference,
    publish_region_diff,
    verify_step,
)
```

### Canonical Reference

```python
result = prepare_reference(raw_scene, experiment / "input")
```

Preparation evaluates scene transforms and instances, removes only strictly
zero-area triangles, uses float64 geometry, centers evaluated bounds, and
applies one uniform inverse-max-extent scale. It publishes `reference.ply`,
`normalization.json`, captured geometry dependencies, and `input.json` as one
immutable directory.

### Measured Step

```python
result = measure_step(
    canonical_reference=experiment / "input",
    candidate_mesh=candidate,
    output=experiment / "voxblame",
    step=3,
    compare_to=1,
)
```

Step 0 uses `compare_to=None`. Every nonzero step names an explicit earlier
parent. The candidate is never fitted or normalized. Boundary-crossing
triangles contribute to both interior and exterior evidence; fully exterior
geometry is a valid bad candidate rather than malformed input.

The result publishes:

- authoritative integer surface counts at depths 1 through 8;
- exact depth-8 missing and excess sets;
- exact and diagnostic Exterior Surface evidence;
- the complete stable Repair Target order and exact masks;
- candidate interior, exterior, and combined Observable Geometry identities;
- three objective acceptance facts.

### Repair Targets and Region Diff

```python
page = page_repair_targets(experiment / "voxblame", step=3, offset=8)

diff = publish_region_diff(
    experiment / "voxblame",
    from_step=1,
    to_step=3,
    repair_plan=experiment / "work/repair-batch.json",
    output=experiment / "work/region-diff.json",
)
```

Paging revalidates frozen artifacts and never remeasures. Region Diff binds one
Repair Batch and one explicit parent/child edge, preserves interior and
exterior domains, and reports exact-mask, halo, outside-selected, component,
and trajectory facts without Agent judgment.

### Rebuild verification

```python
result = verify_step(
    experiment / "input",
    rebuilt_mesh,
    experiment / "voxblame",
    against_step=3,
    output=experiment / "work/verification.json",
)
```

Verification recomputes temporary evidence, compares interior, exterior,
multiresolution, and combined Observable Geometry identities, and publishes no
Measured Step. Route build manifests separately prove source-to-artifact
derivation.

## Invariants

- Coordinate contract is `trellis2_canonical/1`, cube `[-0.5, 0.5]^3`,
  boundary epsilon `1e-9`, and maximum depth 8.
- Integer occupancy evidence and persisted set identities are authoritative.
- Repair Target masks are disjoint and their union is the complete current
  depth-8 error set.
- Display order is deterministic but non-prescriptive.
- Published directories are immutable; identical retries are idempotent and
  conflicting retries fail closed.
- Public measurement defaults to the native C++ occupancy backend and fails
  closed if the extension is unavailable. Python occupancy is selected only
  explicitly for parity and tests.
- Unknown, corrupt, or mixed measurement documents receive
  `unsupported_or_invalid_voxblame_state`.
