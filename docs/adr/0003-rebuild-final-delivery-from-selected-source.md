# Rebuild final delivery from selected source

Status: Accepted

Date: 2026-08-07

## Decision

Final Delivery is rebuilt from the Selected Step's archived source in isolated
staging, then checked with non-publishing VoxBlame verification before atomic
publication. Rebuilt STEP or implicit artifacts and their derived GLB become
the delivered files only when their Observable Geometry exactly matches the
Selected Step.

## Consequences

Finalization cannot edit source, silently fall back to historical artifacts,
or become an unmeasured Repair Cycle. Source reproducibility is delivery
integrity, while Observable Geometry equality does not claim byte, topology,
BRep, or continuous-surface identity.
