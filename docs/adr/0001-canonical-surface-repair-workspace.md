# Canonical surface repair workspace

Status: Accepted

Date: 2026-08-07

## Decision

Mesh-to-CAD reconstruction uses a unitless Trellis2-normalized Canonical
Reference, exact multiresolution surface occupancy, and an immutable Measured
Step/Repair Cycle graph rather than sampled mesh distances or a linear
latest-file loop.

VoxBlame owns objective measurement, Repair Targets, Region Diff facts, and
Observable Geometry identity. Mesh-to-CAD owns Repair Batches, Planned Edits,
assessment, and Selected Step choice. Route adapters own builds. The workspace
helper owns Attempt, Repair Cycle, Measured Step, and Final Delivery
publication, validation, and Git evidence.

## Consequences

Acceptance remains objective while the Agent may batch coherent CAD edits and
choose an unaccepted result when strict depth-8 equality cannot be reached.
Numeric step adjacency never substitutes for explicit ancestry.
