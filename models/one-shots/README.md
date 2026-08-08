# One-Shots

Single-session generative CAD models. Flat `<name>.step.py` generators (plus
optional `<name>.params.js` viewer parameter/animation sidecars) are
self-contained concepts produced end-to-end in one agent session; folder
entries are larger single-session packages (reconstructions and robot
descriptions) with their own READMEs.

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
- `mars_rover_concept`: NASA/JPL-inspired full Mars rover on a terrain
  diorama — rocker-bogie suspension, six grousered wheels with four-corner
  steering, cutaway body with avionics/battery/science internals, mast with
  stereo head, five-joint arm with instrument turret, HGA/UHF/whip antennas,
  solar wings, and RTG. 18 geometry parameters in the generator plus a
  27-parameter sidecar with nine looped animations (terrain traverse, steering
  sweep, suspension cycle, mast scan, arm deploy, antennas + solar, cutaway
  reveal, exploded assembly, grand tour).

## SpaceX reconstruction packages

> **Educational, non-functional public-source reconstructions. Not suitable
> for manufacture, propulsion, testing, or operational engineering.**

Museum/documentary-style CAD packages reconstructed exclusively from public
sources; proprietary internals are deliberately excluded and hidden internals
appear only as simplified translucent placeholder volumes. Each package's
`PROVENANCE.md`, `DIMENSIONS.md`, and `RESEARCH.md` carry the source,
confidence, and dimension tables.

- [raptor2/](raptor2/README.md): Raptor 2 — exterior, schematic cutaway,
  exploded view, and derived Raptor Vacuum generators.
- [starship/](starship/README.md): Starship / Super Heavy full stack (pinned
  V2/Block 2) — booster, ship, stack, cutaway, and exploded generators reusing
  the raptor2 engines as linked instanced subassemblies.
- [merlin1d/](merlin1d/README.md): Merlin 1D — exterior, schematic cutaway,
  and exploded generators (~260–275 named parts each).
- [falcon_heavy/](falcon_heavy/README.md): Falcon Heavy full vehicle — three
  cores with 27 linked Merlin 1D instances, MVac-derivative second stage,
  cutaway and exploded views (~2,150 named parts each).

## Robot description packages

- [juno/](juno/README.md): Juno humanoid — full biped robot description with
  per-link STEP generators, 3MF meshes, URDF/SRDF, and a parameter sidecar.
- [lyra/](lyra/README.md): Lyra dexterous hand — five-digit robot hand with
  per-link STEP generators, 3MF meshes, URDF/SRDF, and a parameter sidecar.
