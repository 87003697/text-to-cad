# VoxBlame Architecture

VoxBlame localizes conservative surface-occupancy differences between a fixed
reference mesh and a sequence of candidate meshes. It complements sampled
Chamfer and percentile metrics; it does not replace exact distance metrics,
solid inside/outside tests, or CAD feature provenance.

The subsystem is organized as a one-way pipeline:

```text
mesh inputs
  -> reference-owned frame
  -> hierarchical surface voxelization
  -> immutable SurfaceTree / .vbsvo snapshots
  -> reference/candidate grading
  -> previous/current change analysis
  -> report + compact summary
  -> atomic workflow publication
  -> mesh-compare CLI
```

## Package layout

| Module | Responsibility |
|---|---|
| `errors.py` | Shared `VoxBlameError`, `OctreeError`, and `SurfaceTreeError` hierarchy. |
| `frame.py` | Reference-owned world ↔ canonical-lattice transform and mesh vertex validation. |
| `tree.py` | Immutable logical `SurfaceTree`, tree invariants, traversal, digest, and test/debug leaf iteration. |
| `codec.py` | Strict `.vbsvo` v1 byte encoding, decoding, file reads, and file writes. |
| `voxelize.py` | Mesh-to-tree adapter, Python hierarchical SAT builder, and optional native dispatch. |
| `_native.cpp` | C++17 implementation of the same hierarchical conservative SAT builder. |
| `grading.py` | First-mismatch grading, iteration change overlay, region bounds, and bounded next-action selection. |
| `reporting.py` | Projection of domain objects into `voxblame.report/2` and `voxblame.summary/1` JSON. |
| `contracts.py` | Closed-world validators for the frozen replacement canonical session, report, and summary shapes. |
| `prepare_reference.py` | Raw input capture, evaluated-scene normalization, identity, validation, and atomic Canonical Reference publication. |
| `exterior.py` | Canonical-cube clipping, exact exterior facts, signed diagnostic occupancy, and snapshot identity. |
| `targets.py` | Complete 18-connected error partitioning, stable target identities, exact mask artifacts, and deterministic paging. |
| `measurement.py` | Multiresolution interior/exterior measurement and atomic Measured Step publication. |
| `store.py` | Filesystem repository, strict loads, idempotent retry, and atomic session/step publication. |
| `session.py` | Application orchestration exposed as `run_step(...)`. |
| `__init__.py` | Curated public API. |

Legacy `meshscope.surface_tree` and `meshscope.octree_error` are thin
compatibility facades. New production code should import from
`meshscope.voxblame`.

The replacement shapes are frozen in [CONTRACT.md](CONTRACT.md). They
intentionally live beside the current production readers until the later
atomic cutover; the runtime sections below describe the pre-cutover path.

The old flat Morton implementation is not production code. The oracle used to
prove parity lives under:

```text
tests/python/packages/meshscope/support/morton_oracle.py
```

Production modules must not import that test helper.

## Stable public interfaces

### Canonical Reference preparation

The replacement workflow starts beside the legacy runtime with one public,
atomic preparation command:

```bash
.venv/skills/mesh-compare/bin/python skills/mesh-compare/scripts/mesh-compare \
  voxblame-prepare-reference raw-scene.glb --output EXP/input
```

On success, `EXP/input` appears as one publication containing the captured raw
entry and geometry dependencies, `reference.ply`, `normalization.json`, and
`input.json`. The authoritative PLY is binary little-endian float64 triangle
geometry in the fixed Trellis2 canonical cube. Failures leave `EXP/input`
absent and write bounded evidence to `EXP/input.failure.json`.

The corresponding Python boundary is:

```python
from meshscope.voxblame import prepare_reference

result = prepare_reference(raw_scene, output_directory)
manifest = result.manifest
```

### Canonical Measured Step

The replacement measurement boundary accepts any candidate already expressed
in Canonical Reference coordinates, including boundary-crossing and fully
exterior geometry:

```python
from meshscope.voxblame import measure_step

result = measure_step(
    canonical_reference=experiment / "input",
    candidate_mesh=candidate,
    output=experiment / "voxblame",
    step=0,
)
```

Triangles that cross `[-0.5, 0.5]^3` are geometrically partitioned before
occupancy is built. Interior fragments supply the depth-1 through depth-8
surface evidence. Exterior fragments supply exact containment, canonical
bounds, centroid, outside directions, and nearest/farthest L-infinity overrun,
plus a signed diagnostic grid. Resource limits may reduce only the diagnostic
grid depth; exact facts remain unchanged. The persisted exterior snapshot and
its resolution metadata participate in the Observable Geometry identity. The
signed grid-depth exponent may pass below zero for very distant geometry, so
the diagnostic mask stays resource-bounded without weakening containment.

VoxBlame publishes objective facts rather than an `accepted` verdict. Complete
acceptance requires all three facts to be true: exact depth-8 equality
(`global_depth_8_zero`), a clear exterior (`out_of_frame_clear`), and no
unresolved evidence conflict (`no_evidence_conflict`).

Exact containment, signed occupancy, snapshot bytes, resolution metadata, and
their identities are cross-checked before publication; any conflict fails the
measurement instead of publishing a Measured Step with contradictory facts.

Each Measured Step also freezes the complete Repair Target order. Interior
missing/excess cells are grouped over their union with 18-connectivity. The
versioned `repair_target_partition/1` profile splits components larger than
4096 cells at depth-4 coarse-octree locality boundaries while keeping every
resulting chunk connected. Exact minimal-cover masks are published under the
step's `targets/` directory. Exterior diagnostic occupancy is retained as a
separate target when present. Target identity binds the source step, exact mask,
missing/excess direction counts, and split provenance.

The measurement summary returns the first eight targets. Read later pages from
the immutable publication without remeasurement:

```python
from meshscope.voxblame import page_repair_targets

page = page_repair_targets(experiment / "voxblame", step=0, offset=8)
```

The corresponding CLI is `mesh-compare voxblame-targets --output EXP/voxblame
--step 0 --offset 8`. Follow `next_offset` until it is null. `display_rank` is
non-prescriptive; `error_profile` records missing/excess direction facts.

### Application entry point

```python
from meshscope.voxblame import run_step

summary = run_step(
    reference_mesh,
    candidate_mesh,
    state_dir,
    step,
    max_depth=8,
    compare_to=None,
)
```

This is the only interface required by the `mesh-compare` CLI. It returns a
compact `voxblame.summary/1` dictionary and publishes the full immutable
evidence below `state_dir`.

### Tree construction

```python
from meshscope.voxblame import CanonicalFrame, voxelize_mesh

frame = CanonicalFrame.from_reference(reference)
tree = voxelize_mesh(candidate, frame, max_depth=8, backend="auto")
```

`backend` may be:

- `"auto"` — use the native extension when importable, otherwise Python.
- `"python"` — force the correctness fallback.
- `"native"` — require the C++ extension and fail if unavailable.

For already transformed lattice-space triangles:

```python
tree = build_lattice_tree(triangles, max_depth=8, backend="auto")
```

### Tree and codec

```python
from meshscope.voxblame import (
    SurfaceTree,
    decode_surface_tree,
    encode_surface_tree,
    read_surface_tree,
    write_surface_tree,
)
```

The byte-oriented functions are pure format operations and are preferred for
golden/fuzz tests. File-oriented functions are thin filesystem adapters.

### Grading

```python
errors = grade_surface_trees(reference_tree, candidate_tree)
changes = compare_error_trees(previous_errors, errors, max_depth)
action = select_next_action(changes, errors, frame)
```

Domain objects remain typed until `reporting.py` converts them to versioned
JSON:

- `RegionHandle`
- `ErrorCell`
- `ChangeCell`
- `NextAction`

## Core data contracts

### Canonical frame

The reference mesh exclusively defines:

```text
center = reference bounding-box center
scale  = maximum reference bounding-box extent
lattice = [-0.5, 0.5]^3
```

Every candidate and historical step is evaluated in that same frame. Candidate
geometry outside the root cube is ignored; in-frame intersections are still
graded. A fully out-of-frame candidate therefore produces a valid empty tree,
not a session error.

### SurfaceTree

`SurfaceTree` contains:

```python
SurfaceTree(
    max_depth: int,
    masks: bytes,
    spans: np.ndarray,  # little-endian uint32, immutable backing storage
    leaf_count: int,
)
```

- One mask represents one internal octree node.
- Child bits use `(x << 2) | (y << 1) | z`.
- Internal nodes are stored in child-order preorder.
- A max-depth leaf is represented only by its parent mask bit.
- `spans[i]` is the number of internal-node rows in node `i`'s subtree,
  including itself.
- The empty tree is one zero root mask with span 1 and leaf count 0.
- Non-root zero masks are invalid.

Masks are the logical identity. Spans are a derived acceleration index and are
recomputed during validation.

### Logical digest

The tree digest is:

```text
SHA256(
  b"voxblame.svo/1\0"
  + uint8(max_depth)
  + uint8(xyz_child_order)
  + masks
)
```

Spans are deliberately excluded. A digest comparison is meaningful only with
the accompanying storage schema, max depth, and reference frame.

### `.vbsvo` v1

The binary snapshot is:

```text
56-byte little-endian header
uint8 masks[node_count]
little-endian uint32 spans[node_count]
```

The decoder validates:

- magic, version, child order, flags, and depth
- node-count bounds and exact byte length
- complete preorder node consumption
- non-root mask rules
- recomputed spans and leaf count
- logical digest

Malformed input fails closed with `SurfaceTreeError`.

## Complete workflow

### 1. Load and identify meshes

`session.run_step()` loads both meshes, validates finite triangle geometry, and
computes source digests. File inputs use the exact source bytes; in-memory mesh
inputs use canonical little-endian triangle bytes.

### 2. Initialize or validate the session

For a new state directory:

```text
reference mesh
  -> CanonicalFrame
  -> voxelize_mesh(reference)
  -> reference.vbsvo
  -> session.json
```

For an existing session, the store validates:

- `voxblame.session/2`
- max depth
- reference frame
- reference source digest
- strict `reference.vbsvo` decode
- reference logical metadata

The experimental `voxblame.session/1` format is rejected rather than migrated.

### 3. Build the candidate tree

If reference and candidate source digests match, the immutable reference tree
is reused. Otherwise, `voxelize_mesh()`:

1. transforms triangles into the canonical frame;
2. discards zero-area triangles;
3. recursively runs conservative triangle/AABB SAT;
4. emits preorder masks directly without materializing Morton leaves;
5. validates native or Python output through `SurfaceTree`.

### 4. Grade against the reference

`grade_surface_trees()` synchronously traverses both trees:

```text
reference occupied, candidate empty -> missing
reference empty, candidate occupied -> excess
both empty                           -> skip
both occupied                        -> descend
```

Traversal stops at the first mismatch on each branch. The result is an
adaptive, non-overlapping set of `ErrorCell` values.

### 5. Compare with a previous step

For `compare_to`, the selected historical candidate snapshot is loaded and
graded against the same reference:

```text
reference vs previous -> previous errors
reference vs current  -> current errors
previous/current overlay -> changes
```

Changes are classified as:

- `introduced`
- `regressed`
- `changed`
- `improved`
- `resolved`

The workflow does not define progress by directly comparing candidate trees;
both states are interpreted relative to the fixed reference.

### 6. Select one bounded action

`select_next_action()` selects one deterministic action in this order:

```text
regressed -> introduced -> changed -> remaining
```

The action includes:

- reason
- missing/excess direction
- first-error depth
- stable `{depth, octant_prefix}` region handle
- world-space AABB

The agent receives this single bounded action, not the full error arrays.

### 7. Build report and summary

`reporting.py` produces:

- `voxblame.report/2` — full errors, changes, metadata, and overview
- `voxblame.summary/1` — compact provenance, counts, coarse depth, and one
  next action

Grading code does not know JSON field names. Schema changes belong in
`reporting.py`.

### 8. Atomically publish

`VoxBlameStore` first writes:

```text
.tmp-<step>-<uuid>/
  candidate.vbsvo
  report.json
```

It reloads and validates both artifacts before renaming the directory to:

```text
steps/<step>/
```

An existing identical step is an idempotent retry. An existing step with
different content is rejected. `.tmp-*` directories are intentionally retained
as ignored crash evidence.

## Dependency direction

Keep dependencies one-way:

```text
errors
  ├── frame
  ├── tree <- codec
  └── tree + frame <- voxelize

tree + frame <- grading <- reporting
tree + exterior <- targets

codec + voxelize + grading + reporting <- store/session <- CLI
```

Important boundaries:

- `tree.py` must not import voxelization, grading, reporting, or session code.
- `codec.py` may depend on the tree model, but the tree model does not depend
  on the codec.
- `voxelize.py` returns a validated tree and does not know persistence or JSON.
- `grading.py` is pure tree/domain logic and does not read files.
- `reporting.py` owns versioned JSON.
- `targets.py` owns target partition, exact target-mask identity, and paging.
- `store.py` owns filesystem publication.
- `session.py` composes the modules.
- CLI code imports only `run_step`.
- Production code never imports the flat Morton test oracle.

## Where to make changes

| Desired change | Module |
|---|---|
| Change frame construction or transforms | `frame.py` |
| Change tree representation or traversal | `tree.py` |
| Change `.vbsvo` bytes or validation | `codec.py` |
| Change SAT or backend dispatch | `voxelize.py` and `_native.cpp` |
| Change mismatch or progress semantics | `grading.py` |
| Change Repair Target partition or paging | `targets.py` |
| Change report/summary fields | `reporting.py` |
| Change retry or atomic publication | `store.py` |
| Change end-to-end step orchestration | `session.py` |
| Change CLI flags | `skills/mesh-compare/scripts/mesh-compare/cli.py` |
| Change flat-oracle parity tests | test support `morton_oracle.py` |

## Test strategy

Focused package tests cover:

- `.vbsvo` golden bytes and corruption rejection
- immutable tree invariants
- randomized Morton-set/tree parity
- flat SAT vs Python hierarchy parity
- Python/native bit parity
- first-mismatch grading
- error-tree overlay and action priority
- session mismatch and `session/1` rejection
- arbitrary `compare_to`
- immutable retry and crash residue
- fully out-of-frame candidates
- report/snapshot digest agreement
- complete, disjoint 18-connected Repair Target partitioning and stable paging

The production bundle intentionally excludes `.so`, `.dylib`, and `.pyd`.
Therefore verification must include both:

1. native import/parity in an editable build environment; and
2. Python fallback in a clean physical skill bundle.
