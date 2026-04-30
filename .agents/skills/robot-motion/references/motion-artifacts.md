# Motion Artifact Envelopes

Use `gen_motion()` when a URDF should expose inverse kinematics or path planning through CAD Explorer and the local motion server. The URDF can come from any source, as long as it is a valid repo-local `.urdf` and the motion config references real robot links, joints, frames, and end effectors.

## Source Shape

Define a zero-arg `gen_motion()` in the Python file that owns the URDF, or in a nearby Python source:

```python
def gen_motion() -> dict[str, object]:
    return {
        "urdf": "sample_robot.urdf",
        "provider": "moveit_py",
        "commands": ["urdf.solvePose", "urdf.planToPose"],
        "planningGroup": "arm",
        "jointNames": ["joint_1", "joint_2", "joint_3"],
        "planningGroups": [
            {
                "name": "arm",
                "jointNames": ["joint_1", "joint_2", "joint_3"],
            }
        ],
        "endEffectors": [
            {
                "name": "tool",
                "link": "tool_link",
                "frame": "base_link",
                "parentLink": "tool_mount_link",
                "planningGroup": "arm",
                "positionTolerance": 0.002,
            }
        ],
        "planner": {
            "pipeline": "ompl",
            "plannerId": "RRTConnectkConfigDefault",
            "planningTime": 1.0,
        },
        "disabledCollisionPairs": [
            ["link_2", "link_3"],
        ],
    }
```

## Fields

- `urdf`: path to the generated `.urdf`, relative to the Python source file.
- `provider`: currently `moveit_py`.
- `commands`: use `["urdf.solvePose"]` for pose-solve-only; add `urdf.planToPose` for planning.
- `planningGroup`: MoveIt planning group name for the active arm or chain.
- `jointNames`: non-fixed URDF joints in planning-group order. Revolute values are degrees on the websocket wire and radians in generated SRDF group states.
- `planningGroups`: optional array of MoveIt planning groups. Use this when one URDF exposes multiple independently solved chains, such as two arms with separate TCPs. When omitted, `planningGroup` and `jointNames` define the only group.
- `endEffectors`: one or more named tool links. Multiple end effectors are valid when they represent distinct tools or TCPs; each should have a stable `name`, controlled `link`, target `frame`, and MoveIt `parentLink`. For multi-group envelopes, set `planningGroup` on each end effector so the motion server uses the matching solver group.
- `planner`: required only for `urdf.planToPose`.
- `disabledCollisionPairs`: extra link pairs to disable in SRDF. Adjacent parent/child link pairs are added automatically from URDF joints.
- `groupStates`: optional SRDF group states. Each entry uses `jointValuesByNameRad`; include `planningGroup` when multiple groups are defined.

## Generated Files

Run:

```bash
python .agents/skills/robot-motion/scripts/gen_motion_artifacts/cli.py sample_robot.py --summary
```

The generator writes all motion-owned outputs under `.<urdf filename>/robot-motion/`:

- `explorer.json`
- `motion_server.json`
- `moveit2_robot.srdf`
- `moveit2_kinematics.yaml`
- `moveit2_py.yaml`
- `moveit2_planning_pipelines.yaml` when `urdf.planToPose` is enabled

Regenerate motion artifacts after changing planning joints, end effectors, collision exclusions, planner settings, or the URDF links/joints they reference.
