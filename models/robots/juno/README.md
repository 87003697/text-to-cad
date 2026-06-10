# juno — compact humanoid robotics platform concept

A sleek research-humanoid CAD concept with Unitree-G1-like proportions:
~1.40 m tall, athletic ready stance, exposed cylindrical actuator modules at
every joint, warm-porcelain composite shells over graphite structure with
machined-aluminum joint rims, coral-orange accents on repeated functional
details (actuator hubs, toe/heel bumpers, head vents, fingertip pads), a
gloss midnight-blue sensor visor displaying cyan pixel-grid eyes (Anki
Cozmo style), and dexterous five-digit hands. Clean industrial design,
no logos.

## Degrees of freedom (27 body DOF)

| Group | Joints | DOF |
| --- | --- | --- |
| Each leg (x2) | hip yaw, hip roll, hip pitch, knee, ankle pitch, ankle roll | 12 |
| Each arm (x2) | shoulder pitch, shoulder roll, shoulder yaw, elbow, wrist roll, wrist pitch | 12 |
| Waist | yaw | 1 |
| Neck | yaw, pitch | 2 |

Hands add posed (cosmetic) finger articulation on top of the 27 counted DOF.

## Files

- `juno.py` — build123d generator (`gen_step()`); authoritative source.
  Pose angles are module-level parameters; joints are authored as
  `cadpy.assembly.AssemblyHelper` revolute frames driven by those angles.
- `juno_parts/` — part-builder package (sculpted segments, joint hardware,
  shared style library). Each builder returns an identity-location labeled
  compound in its part-local frame.
- `juno.step` — generated STEP assembly (derived artifact).

## Conventions

Units mm. Pelvis waist-yaw joint center is the world origin; +X forward,
+Y robot-left, +Z up. Soles rest near z = -774 in the default stance.
Regenerate with the CAD skill: `python scripts/step models/robots/juno/juno.py`.
