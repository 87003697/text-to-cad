# Canonical VoxBlame contract

Status: Frozen for the canonical repair-workspace implementation.

The executable authorities are `contracts.py` for Measured Step documents and
`region_diff.py` for Repair Batch/Region Diff documents. Every object below is
closed: listed fields are required, unlisted fields are invalid, and there are
no extension fields. A nullable field is still required. Any unsupported
Measured Step schema, legacy shape, mixed shape, corrupt value, or unknown
field has the single public classification:

```text
unsupported_or_invalid_voxblame_state
```

The schema identifiers are deliberately reused in place. Their former shapes
are not valid inputs to this contract.

## Domain language and state graph

The repository glossary in `/CONTEXT.md` is normative. The **Canonical
Reference** is immutable experiment input. A **Measured Step** is a published
candidate node; a **Repair Cycle** is a successful edge from one Measured Step
to another. A failed **Attempt** creates no Measured Step and consumes no Repair
Cycle. A **Repair Target** is objective spatial evidence. An Agent groups one or
more targets into a **Repair Batch**, authors one or more **Planned Edits**, and
then assesses the objective **Region Diff**. The Agent chooses a **Selected
Step**. **Final Delivery** rebuilds that step's source and must reproduce its
**Observable Geometry**.

VoxBlame owns the Canonical Reference identity, Measured Step evidence, Repair
Targets, Region Diff facts, and Observable Geometry identity. It does not own
Repair Batch selection, Planned Edits, cycle assessment, Selected Step choice,
or Final Delivery publication. Those workspace contracts build on the frozen
measurement documents below.

Step 0 has `compare_to: null`. Every nonzero step has an explicit earlier
`compare_to`; numeric adjacency is never ancestry.

## `voxblame.session/2`

| Object | Exhaustive required fields |
|---|---|
| root | `schema`, `coordinate_contract`, `semantic_units`, `max_depth`, `boundary_epsilon`, `canonical_reference`, `profiles` |
| `canonical_reference` | `canonical_reference_sha256`, `reference_ply_path`, `reference_ply_sha256`, `triangle_set_sha256`, `normalization_json_path`, `normalization_json_sha256`, `interior_tree_path`, `interior_tree_sha256` |
| `profiles` | `surface_occupancy`, `target_partition`, `exterior_surface` |

Fixed values are `coordinate_contract: trellis2_canonical/1`,
`semantic_units: null`, `max_depth: 8`, and `boundary_epsilon: 1e-9`. Profile
values are `conservative_surface_occupancy/1`,
`repair_target_partition/1`, and `signed_exterior_surface/1`. Paths are
normalized experiment-relative POSIX paths. Identities are lowercase SHA-256
digests.

## `voxblame.report/2`

| Object | Exhaustive required fields |
|---|---|
| root | `schema`, `coordinate_contract`, `max_depth`, `step`, `compare_to`, `canonical_reference`, `measurement`, `errors_by_depth`, `depth_8_evidence`, `exterior_surface`, `repair_targets`, `objective_facts`, `no_observable_geometry_change` |
| `canonical_reference` | `canonical_reference_sha256`, `reference_ply_sha256`, `triangle_set_sha256`, `interior_tree_sha256` |
| `measurement` | `candidate_mesh_sha256`, `interior_tree_sha256`, `exterior_snapshot_sha256`, `observable_sha256` |
| each `errors_by_depth[]` | `depth`, `reference_surface_count`, `candidate_surface_count`, `missing_surface_count`, `excess_surface_count`, `union_surface_count`, `surface_error_count`, `surface_error_rate` |
| `depth_8_evidence` | `missing_surface`, `excess_surface` |
| each depth-8 set | `storage_schema`, `path`, `logical_sha256`, `surface_count` |
| `exterior_surface` | `storage_schema`, `path`, `logical_sha256`, `surface_present`, `surface_cell_count`, `bounds_canonical`, `centroid_canonical`, `nearest_overrun`, `farthest_overrun`, `outside_directions`, `diagnostic_grid_depth`, `coarsened` |
| `repair_targets` | `ordering_profile`, `total`, `ordered_targets` |
| each target | `target_key`, `source_step`, `kind`, `display_rank`, `bounds_canonical`, `error_profile`, `mask`, `component`, `exterior` |
| target `error_profile` | `missing_surface_count`, `excess_surface_count`, `surface_error_count` |
| target `mask` | `storage_schema`, `path`, `logical_sha256`, `region_count` |
| target `component` | `component_key`, `split_index`, `split_count`, `split_reason` |
| exterior-target `exterior` | `centroid_canonical`, `surface_cell_count`, `nearest_overrun`, `farthest_overrun`, `outside_directions`, `diagnostic_grid_depth`, `coarsened` |
| `objective_facts` | `global_depth_8_zero`, `out_of_frame_clear`, `no_evidence_conflict` |
| every bounds object | `min`, `max` |

`errors_by_depth` contains exactly eight entries in depth order 1 through 8.
Integer counts are authoritative. `surface_error_count` equals missing plus
excess. `surface_error_rate` equals error divided by union, or zero for an empty
union. The two depth-8 set counts must match the depth-8 entry.

`exterior_surface` is always present. When clear, its count is zero, directions
are empty, and bounds/centroid/overruns are null. When occupied, those facts are
required and `out_of_frame_clear` is false. Its logical identity must match
`measurement.exterior_snapshot_sha256`.

The report freezes the complete target order. Interior targets have
`exterior: null`; exterior targets require the complete diagnostic object.
`display_rank` is a stable order, not priority.

`repair_target_partition/1` groups the union of missing and excess depth-8
cells with 18-connectivity. Components above 4096 cells split deterministically
at depth-4 coarse-octree locality boundaries; split chunks remain connected.
Target masks are exact minimal octree covers. `error_profile` carries the
missing/excess direction facts without introducing a prescriptive direction,
priority, strategy, verdict, or `next_action` field.

An interior `octree_region_set/1` mask is canonical JSON containing
`schema`, `max_depth`, and ordered `{depth, prefix}` regions; no region overlaps
another and expanding the minimal cover reproduces the target's exact depth-8
cells. An exterior `exterior_grid_region_set/1` mask contains the frozen signed
diagnostic grid depth and ordered cells from the authoritative exterior
snapshot. Their domain-separated logical digests bind the target keys together
with source step, missing/excess counts, component key, and split provenance.
Paging revalidates these artifacts before returning any frozen report slice.

### Exterior snapshot identity

Each Measured Step persists an authoritative
`voxblame.exterior-snapshot/1`. Its closed implementation shape records:

- the coordinate contract and `boundary_epsilon`;
- exact surface presence, clipped exterior bounds and area centroid,
  nearest/farthest L-infinity overrun, and canonically ordered outside
  directions;
- signed diagnostic cells plus a resolution object containing the grid profile,
  depth, canonical cell size and origin, index mapping, boundary policy, and
  `coarsened` fact.

The logical snapshot digest is SHA-256 over the canonical JSON bytes with the
`voxblame.exterior-snapshot/1\0` domain prefix. Resource pressure may reduce
only diagnostic grid depth and cells; it does not change the exact facts.
Diagnostic grid depth is a signed base-2 resolution exponent: depth 8 aligns
to canonical depth-8 cells, depth 0 uses canonical-cube-sized cells, and
negative depths remain valid for bounded evidence of very distant geometry.
`measurement.observable_sha256` binds the interior-tree digest, authoritative
exterior-snapshot digest, exterior profile, and complete exterior-resolution
object.

Snapshot bytes, logical identity, exact containment, outside directions,
diagnostic occupancy, and coarsening metadata are cross-checked before a
Measured Step is published. A conflict fails publication; therefore a
published step may truthfully carry `no_evidence_conflict: true`. Preview and
measurement identity conflicts remain a later pre-publication cross-check and
likewise must not publish a conflicting Measured Step.

## `voxblame.summary/1`

| Object | Exhaustive required fields |
|---|---|
| root | `schema`, `coordinate_contract`, `max_depth`, `step`, `compare_to`, `report`, `canonical_reference`, `measurement`, `errors_by_depth`, `exterior_surface`, `repair_targets`, `objective_facts`, `no_observable_geometry_change` |
| `canonical_reference` | same four fields as report |
| `measurement` | same four fields as report |
| each `errors_by_depth[]` | same eight fields as report |
| `exterior_surface` | same twelve fields as report |
| `repair_targets` | `ordering_profile`, `total`, `returned`, `remaining`, `offset`, `next_offset`, `items` |
| each target item | same target fields as report |
| summary target `mask` | `storage_schema`, `logical_sha256`, `region_count` |
| `objective_facts` | `global_depth_8_zero`, `out_of_frame_clear`, `no_evidence_conflict` |

The summary repeats objective evidence rather than inventing a score. It may
contain at most eight target items. The page is an exact path-free projection
of the corresponding slice in the report's frozen order.

## `voxblame.repair-batch/1`

A Repair Batch is Agent-authored input to Region Diff. Its closed root contains
`schema`, `from_step`, `selected_targets`, `planned_edits`, `rationale`, and
`preview_observation`.

- `selected_targets` contains one or more unique
  `{target_key, mask_sha256}` identities from `from_step`.
- `planned_edits` contains one or more unique stable `edit_key` values, a
  non-empty `target_keys` subset, and a short `description`.
- Every selected target is mapped by at least one Planned Edit. Edits and
  targets may map many-to-many.

The plan digest is SHA-256 over canonical JSON bytes with the
`voxblame.repair-batch/1\0` domain prefix. It therefore freezes target
selection, mask identities, Planned Edit keys and mappings, rationale, and the
pre-edit preview observation without copying Agent-authored judgment into
objective evidence fields.

## `voxblame.region-diff/1`

The public Region Diff binds one explicit published edge (`from_step` to a
Measured Step whose `compare_to` is that source), the Repair Batch digest,
selected target/mask identities, and Planned Edit mappings. Its required
evidence is:

- global depth-1 through depth-8 before/after/delta trajectories and
  Observable Geometry identities;
- each selected target's fixed source-step exact mask and its one-cell halo;
- an interior and exterior batch union kept in their native domains;
- interior and signed-exterior outside-selected counts, deltas, new error
  counts, and up to three largest spatial components;
- exact exterior containment facts when an exterior target is selected.

Interior coarse-depth target metrics use explicit depth-8-equivalent counts;
they are never serialized as native coarse surface-cell counts. Interior halo
is fixed at depth 8. Exterior exact and halo evidence uses the selected
target's frozen signed-grid depth. Bounds are diagnostic; selected membership
always comes from exact masks.

With a fixed Canonical Reference, one spatial cell cannot literally change
from missing to excess or vice versa. Direction transitions are therefore
reported without false cell pairing: over each selected exact-mask-plus-halo
region, the evidence separately counts source-direction cells absent afterward
and new opposite-direction cells absent from the source error set. Both ends
are objective; the tool does not infer that one particular cell caused another.

Finer exterior snapshots project exactly to a selected target's frozen grid.
A later snapshot coarser than that frozen grid cannot supply authoritative
fine-cell exact/halo counts and fails closed. When no exterior target is
selected, outside-selected evidence uses the coarser common grid of the two
snapshots, so neither side is expanded into invented child occupancy.

The artifact identity is SHA-256 over the complete canonical Region Diff
document before its `identity` object, with the `voxblame.region-diff/1\0`
domain prefix. Publication uses an atomic no-clobber link: identical concurrent
publication is idempotent and conflicting publication cannot replace the
winner. The contract emits objective counts and identities only. It never labels
a change improved, regressed, resolved, introduced, keep, or revert.

## Forbidden fields

These names are forbidden at every nesting level. Closed-object validation
also rejects every other unlisted name.

```text
accepted
best_step
bounds_world
candidate
candidate_digest
chamfer
chamfer_distance
change_counts
changes
coarsest_first_error_depth
current
current_error
direction
distances
errors
first_error_depth
final_step
frame
hausdorff
hausdorff_distance
heatmap
measurement_contract
morton_prefix
next_action
octant_prefix
overview
p90
p95
priority
previous_error
reference
region_handle
remaining_error_count
sample_count
sample_seed
samples
selected_best_step
stop_reason
stats
strategy
verdict
```

`accepted`, repair strategy, keep/revise/revert assessment, workflow stop
reason, Selected Step, and Final Delivery belong to Agent/workspace artifacts,
not VoxBlame measurement output.

## Cross-document invariants

The bundle validator requires session, report, and summary Canonical Reference
identities to agree. Report and summary must agree exactly on ancestry,
measurement identities, ordered multiresolution evidence, exterior evidence,
objective facts, and no-op fact. The summary page must be the compact
projection of the report target slice.
