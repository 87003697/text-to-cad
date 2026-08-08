# Mesh Reconstruction Evaluation

This context describes the language used by the mesh-to-CAD reconstruction loop, its VoxBlame evidence, and its persistent experiment workspace.

## Language

**Canonical Reference**:
The evaluated input triangle surface after one Trellis2 max-extent normalization into the fixed `[-0.5, 0.5]^3` coordinate space.
_Avoid_: Ground truth in world units, normalized candidate

**Measured Step**:
An immutable candidate state whose preview and VoxBlame evidence were successfully published. Step 0 is the initial candidate; repair cycle N produces step N.
_Avoid_: Iteration, latest model

**Repair Cycle**:
An immutable successful edge from one measured step to another, containing the frozen plan, direct region diff, source change evidence, and Agent assessment.
_Avoid_: Iteration, attempt

**Attempt**:
A frozen-plan execution that tries to produce a measured step and may fail without consuming a repair cycle.
_Avoid_: Cycle, step

**Repair Target**:
A source-step spatial partition of surface-occupancy error, identified by a fixed exact mask and diagnostic bounds. It says where the disagreement is, not how to edit CAD.
_Avoid_: Action, command, next action

**Repair Batch**:
One coherent modeling change that selects one or more current-step repair targets and maps planned edits to them.
_Avoid_: Action list, voxel queue

**Planned Edit**:
An Agent-authored CAD change within a repair batch, identified by an `edit_key` and mapped to one or more selected repair targets.
_Avoid_: VoxBlame recommendation

**Region Diff**:
Objective before/after surface-occupancy evidence over the selected targets' fixed masks, their halos, and the remaining space.
_Avoid_: Verdict, rollback decision

**Exterior Surface**:
Candidate surface lying beyond the canonical cube by more than the canonical epsilon. Its existence always vetoes acceptance.
_Avoid_: Outlier, harmless overflow

**Observable Geometry**:
The combined identity of interior surface occupancy, exterior surface occupancy, and exterior resolution metadata.
_Avoid_: Mesh file hash, exact BRep identity

**Accepted Step**:
A measured step whose depth-8 interior surface sets match exactly and whose exterior surface is clear.
_Avoid_: Best step, visually good step

**Selected Step**:
The measured step chosen for final delivery after comparing the plausible candidates. It may remain unaccepted.
_Avoid_: Latest step, accepted step

**Final Delivery**:
The atomically published package rebuilt from the selected step's source and verified to reproduce its observable geometry.
_Avoid_: Copy of the latest files

**Workspace**:
The experiment-local state graph containing immutable measured steps, repair cycles, attempts, VoxBlame evidence, and final delivery, plus an ignored mutable work area.
_Avoid_: Output folder, VoxBlame directory
