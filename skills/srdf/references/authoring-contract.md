# SRDF Authoring Contract

Use this reference when writing or editing SRDF XML directly. The `.srdf` file is the source of truth and must be auditable on its own: linked URDF, planning intent, and provenance all live in the file.

## File Shape

Every authored `.srdf` follows this shape, in this order:

1. XML declaration: `<?xml version="1.0"?>`.
2. Planning-ledger comment block (compact form of `references/planning-ledger.md`).
3. One `<robot>` root with the **same `name` as the linked URDF** and the metadata namespace declared: `<robot xmlns:tcad="https://text-to-cad.dev/srdf" name="...">`.
4. `<tcad:urdf path="..."/>` as the first child.
5. `<virtual_joint>` elements, then `<group>`, `<group_state>`, `<end_effector>`, `<passive_joint>`, `<disable_collisions>` — grouped by element type, in that order.

Keep two-space indentation. Comment nontrivial decisions inline (why a chain tip, why a pair is disabled).

## The URDF Link Element (non-negotiable)

```xml
<robot xmlns:tcad="https://text-to-cad.dev/srdf" name="so101_new_calib">
  <tcad:urdf path="so101.urdf" />
```

- `path` is a POSIX relative path from the `.srdf` file's directory to the `.urdf`; it must stay inside the repository and end in `.urdf`.
- This element is how the CAD Viewer, the local MoveIt2 server, and the bundled validator resolve robot structure. An SRDF without it fails validation and cannot be reviewed.
- Legacy files may carry `<explorer:urdf .../>` (namespace `https://text-to-cad.dev/explorer`); consumers still accept it, but always author new or edited files with `tcad:urdf`.
- Keep the URDF and SRDF in the same directory with the same basename unless the project layout dictates otherwise.

## Names Come From the URDF Table

Every `link`, `joint`, `base_link`, `tip_link`, `parent_link`, and group-state joint name must be copied from the extracted URDF table (see `references/srdf-workflow.md`). Never type a name from memory or from a similar robot; near-miss names (`wrist_roll` vs `wrist_roll_joint`) are the most common SRDF defect and validation will reject them.

## Element Contract

- `<group>`: prefer exactly one `<chain base_link tip_link>` for a serial manipulator — base to tip must be a real parent→child path in the URDF tree. Use explicit `<joint>`/`<link>` members for non-chain groups (grippers, heads), and `<group>` subgroups for unions (dual-arm, whole-body). Do not mix representations in one group without reason.
- `<group_state name group>`: one `<joint name value>` per **movable, non-mimic** joint in the group. Radians for revolute/continuous, meters for prismatic, values within URDF limits.
- `<end_effector name parent_link group parent_group>`: the EE group must not share links with `parent_group`; `parent_link` belongs to the parent group (or is adjacent to the EE group) and is typically the attachment/flange link.
- `<virtual_joint name type parent_frame child_link>`: attaches the robot root to an external frame (`world`). `fixed` for fixed-base arms; `planar`/`floating` only when planning genuinely needs that freedom.
- `<passive_joint name>`: unactuated joints that planners must not command.
- `<disable_collisions link1 link2 reason>`: evidence-backed only; see `references/disabled-collisions.md`. No duplicate or reversed-duplicate pairs.

## Golden Skeleton

```xml
<?xml version="1.0"?>
<!--
  srdf: example_arm | urdf: example_arm.urdf | task: arm IK + gripper control
  groups: arm (chain base_link->tool0), gripper (joint members)
  states: home, ready (radians, within URDF limits)
  disabled collisions: URDF-adjacent pairs only (reason Adjacent)
  assumptions: tool0 is the TCP; no sampled collision matrix yet
-->
<robot xmlns:tcad="https://text-to-cad.dev/srdf" name="example_arm">
  <tcad:urdf path="example_arm.urdf" />
  <virtual_joint name="world_to_base" type="fixed" parent_frame="world" child_link="base_footprint" />
  <group name="arm">
    <chain base_link="base_link" tip_link="tool0" />
  </group>
  <group name="gripper">
    <joint name="finger_joint" />
  </group>
  <group_state name="home" group="arm">
    <joint name="shoulder_pitch" value="0" />
    <joint name="elbow_pitch" value="0" />
    <joint name="wrist_roll" value="0" />
  </group_state>
  <end_effector name="gripper_eef" parent_link="tool0" group="gripper" parent_group="arm" />
  <disable_collisions link1="base_link" link2="shoulder_link" reason="Adjacent" />
</robot>
```

Repository fixtures under `models/robots/` (for example `so101.srdf`, `juno.srdf`) are full worked examples.

## Helper Scripts

Adjacent-pair lists, subgroup unions for many-jointed robots, and degree-to-radian tables are computations: derive them with a short throwaway script over the URDF rather than by hand when the robot has more than a handful of joints. The script is scaffolding; the checked-in `.srdf` remains canonical.
