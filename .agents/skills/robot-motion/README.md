<div align="center">

<img src="assets/robot-motion-demo.gif" alt="Demo of the robot-motion skill solving inverse kinematics in CAD Explorer" width="100%">

<br>

</div>

# Robot Motion Skill

Local inverse-kinematics and path-planning tools for URDF-based robots.

The Robot Motion skill starts from an existing valid URDF and adds motion behavior for CAD Explorer. It owns generated motion artifacts, MoveIt sidecars, local websocket motion-server setup, and browser-driven solve/plan workflows.

## What It Can Do

- Generate CAD Explorer motion artifacts from a Python `gen_motion()` source.
- Create MoveIt/SRDF, kinematics, planning, and motion-server metadata sidecars.
- Run and check the local websocket motion server.
- Solve inverse kinematics for configured end effectors.
- Plan robot motion through the local MoveIt-backed provider.
- Keep motion setup separate from CAD geometry and URDF generation.

## Commands

Run commands from the robot project repository root. If the current working directory is somewhere else, set `ROBOT_MOTION_REPO_ROOT` to the robot project root.

```bash
python .agents/skills/robot-motion/scripts/gen_motion_artifacts/cli.py <robot-urdf-source.py> --summary
```

```bash
.agents/skills/robot-motion/scripts/setup.sh
.agents/skills/robot-motion/scripts/check-motion-server.sh
.agents/skills/robot-motion/scripts/run-motion-server.sh
```

The runtime is Conda/RoboStack/Jazzy-based. Install it only for motion workflows; do not install ROS or MoveIt packages into the repo CAD `.venv`.

For agent-facing workflow rules, use [SKILL.md](SKILL.md). Motion artifact details live in [references/motion-artifacts.md](references/motion-artifacts.md).
