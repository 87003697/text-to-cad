# One-Shots

Single-session generative CAD concepts: each model is one self-contained
`<name>.step.py` generator (plus an optional `<name>.params.js` viewer
parameter/animation sidecar) produced end-to-end in a single agent session.

- `gd01_mecha_concept`: GD01-inspired manned transformable mecha — open-cockpit
  biped with silver roll cage, glossy red armor, black understructure, and a
  parameter sidecar driving the biped-to-quadruped transformation, walk/crawl
  gaits, cockpit access, actuator demo, and exploded reveal.
- `tendon_forearm_hand`: tendon-driven robotic forearm and dexterous humanoid
  hand — actuator cartridge magazine, capstan spools, Bowden-guided colored
  tendons, wrist tendon-differential sheaves and gimbal, translucent cutaway
  shell with service panels, and a parameter sidecar driving sequential finger
  curls, a thumb-opposition grasp, a wrist roll/pitch/yaw differential sweep,
  and a staggered exploded/cutaway reveal (also scrubbable via demo_mode /
  demo_phase).
- `lunar_mass_driver`: SpaceX-inspired lunar mass driver launch complex —
  speculative reusable cargo launcher (not a reconstruction) at compressed
  ~200 m diorama scale: 12 instanced coil segments climbing a vertical-easement
  ramp, levitated sled + cargo canister, retractable gantries, transfer crane,
  Starship-derived lander, control tower, solar farm, radiators, power trunks,
  rovers, drones, dust plumes, and Earth in the sky. All named scene parameters
  live on `MassDriverParams` in the generator; the sidecar adds live controls
  plus exact-loop launch/reload, coil-wave, deploy, patrol, and exploded-reveal
  animations. Sidecar constants mirror the generator defaults, so refresh them
  after rebuilding with different counts.
