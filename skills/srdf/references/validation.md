# SRDF Validation and Verification

Every created or modified `.srdf` runs this recipe before the task is reported complete.

## Recipe

1. **Bundled validator** (always): `python scripts/validate path/to/robot.srdf`. Fix findings and re-run until clean; treat warnings as findings unless the ledger explains them.
2. **Viewer review** (whenever `$cad-viewer` is available): load the SRDF, confirm the linked URDF resolves and renders, and exercise named group states. Include MoveIt2 controls for IK/path review when the task needs them.
3. **MoveIt smoke test** (when a MoveIt environment is available): load the URDF+SRDF pair in MoveIt Setup Assistant or a project launch; solve IK for the primary group; plan to a named state. Report as skipped when unavailable.

## What the Bundled Validator Checks

Structure and linkage:

- root is `<robot>` with a non-empty name; `<tcad:urdf path="..."/>` present (legacy `explorer:urdf` accepted), relative POSIX path, resolving to an existing `.urdf`;
- SRDF robot name matches the URDF robot name;
- unique group, end-effector, group-state, and collision-pair identities.

Against the linked URDF:

- every group joint/link/subgroup name exists (joints in the URDF, links in the URDF, subgroups in the SRDF);
- every chain `base_link`/`tip_link` exists **and** the chain is a real parent→child path in the URDF tree;
- at least one planning group is defined;
- end effectors: group exists, parent group exists when named, parent link exists, no link overlap between EE group and parent group, parent link in parent group or adjacent to the EE group;
- group states: group exists, each joint exists and belongs to the group, no fixed or mimic joints, values within URDF revolute/prismatic limits;
- disabled collisions: both links exist, distinct, non-empty reason, no (reversed) duplicates; warns when 25+ pairs are manually reasoned.

## What Validation Cannot Prove

- That the planning group matches the user's task intent (right arm vs left arm).
- That the TCP/target link is the physically correct tool point.
- That disabled pairs are safe at every reachable configuration — only sampling (Setup Assistant) approaches that.
- That group-state poses are collision-free or useful.

These are semantic decisions: document them in the ledger and verify interactively in the viewer or MoveIt when confidence matters. Visual rendering review alone cannot prove planning correctness.

## Failure Handling

When validation fails against the URDF, decide which side is wrong before editing: a missing name may mean a typo in the SRDF **or** a rename in the URDF that invalidated existing semantics. Fix the owning file (`$urdf` for structure), then re-run the validator on the pair.
