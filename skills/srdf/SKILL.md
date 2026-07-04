---
name: srdf
description: MoveIt2 SRDF authoring, validation, and planning-semantics workflow. Use when creating, editing, inspecting, or validating `.srdf` files, MoveIt planning groups, virtual joints, passive joints, end effectors, group states, disabled collisions, URDF-linked planning semantics, or SRDF handoff for live review. Use the URDF skill for robot structure, the SDF skill for simulator descriptions, and the cad-viewer skill for rendering, live review links, and optional MoveIt2 controls.
---

# SRDF

Provenance: maintained in [earthtojake/text-to-cad](https://github.com/earthtojake/text-to-cad).
Use the installed local skill files as the runtime source of truth; the
repository link is only for provenance and release review.

Use this skill for MoveIt semantic robot descriptions on top of an existing valid URDF. SRDF defines planning semantics; it does not define physical robot structure. The `.srdf` file is the source of truth: author and edit the XML directly. There is no `gen_srdf()` contract.

SRDF correctness is a **planning semantics** problem. The common failure is not invalid XML; it is a plausible SRDF that gives MoveIt the wrong planning group, wrong tool link, wrong default state, unsafe disabled-collision matrix, or wrong joint units. Because language models are weak at spatial and kinematic reasoning, derive planning groups, end effectors, group states, and disabled collisions from the URDF topology, MoveIt Setup Assistant output, sampled collision analysis, or explicit user data. Do not infer them from visual appearance alone — and do not type any link or joint name from memory: extract the URDF's link/joint table first and copy names from it.

## Format boundary

- **URDF** owns physical robot structure: links, joints, geometry, inertials, limits, mimic joints, transmissions, and robot-state publishing.
- **SRDF** owns MoveIt semantics: virtual joints, passive joints, planning groups, group states, end effectors, and disabled collision pairs.
- **SDF** owns simulator/world semantics: physics, sensors, lights, plugins, worlds, and simulation-specific metadata.

Do not place geometry, inertials, joint origins, link poses, mesh references, physical joint limits, transmissions, or `ros2_control` interfaces in SRDF.

## CAD Viewer Handoff

After completing SRDF work that creates or modifies a `.srdf`, you must ALWAYS hand the explicit file path to `$cad-viewer` when that skill is installed. `$cad-viewer` must start CAD Viewer if it is not already running and return link(s) to the relevant created or updated file(s); include optional MoveIt2 controls in the handoff only when the user needs interactive IK or path-planning review. If `$cad-viewer` is unavailable or startup fails, report that instead of silently omitting the handoff.

## Required workflow

1. **Start from a valid URDF.** Author or fix the URDF first with `$urdf` and validate it. The SRDF references that URDF by relative path and every name in the SRDF must exist in it.
2. **Extract the URDF table.** Before writing any SRDF XML, list the URDF's robot name, links, joints (with type, parent, child, limits, mimic flags). Copy names from this table only; never type them from memory. See `references/srdf-workflow.md`.
3. **Identify the planning task.** Record whether the goal is arm IK, gripper control, mobile base planning, dual-arm planning, tool use, or local smoke testing.
4. **Create or update the planning ledger.** Use `references/planning-ledger.md` before writing XML; keep a compact copy as a comment block in the `.srdf`.
5. **Link the URDF.** The root element must declare `xmlns:tcad="https://text-to-cad.dev/srdf"` and the first child must be `<tcad:urdf path="..."/>` with the POSIX relative path to the URDF. This element is how the viewer, the MoveIt2 server, and the validator find the robot structure; without it the SRDF is unusable. See `references/authoring-contract.md`.
6. **Define virtual and passive joints deliberately.** Use them when needed by the robot model.
7. **Define planning groups from URDF topology.** Prefer chain groups for serial manipulators when base/tip form a real parent-to-child path in the URDF tree (the validator verifies this). Use joint/link/subgroup definitions only when they are deliberate.
8. **Define end effectors after group membership is known.** Avoid overlap between an end-effector group and its parent group. Record the actual target/TCP link.
9. **Define group states in URDF-native units.** Revolute and continuous values are radians; prismatic values are meters. Do not store degrees in SRDF. Values must lie within URDF limits and must not set fixed or mimic joints.
10. **Generate disabled collisions from evidence.** Use adjacency derived from the URDF joint table, MoveIt Setup Assistant sampling, or explicit user-provided collision matrices. Do not invent broad disable lists. See `references/disabled-collisions.md`.
11. **Validate every created or modified `.srdf`** with `scripts/validate`; it cross-validates all names, chains, states, and pairs against the linked URDF. Fix findings and re-validate until clean.
12. **Run MoveIt smoke tests when available.** Use MoveIt Setup Assistant or a project MoveIt launch directly.
13. **Report assumptions and skipped checks.** Include incomplete validation, missing MoveIt environment, manually reasoned collision disables, and inferred target links.

## Commands

Run with the Python environment for the project or workspace. Treat `python` in examples as an interpreter placeholder; if bare `python` is unavailable, substitute `python3`, a project virtualenv interpreter, or the configured interpreter path. The validator uses only the Python standard library.

From this skill directory, the validator shape is:

```bash
python scripts/validate path/to/robot.srdf
python scripts/validate path/to/a.srdf path/to/b.srdf
python scripts/validate path/to/robot.srdf --strict
python scripts/validate path/to/robot.srdf --format json
```

The validator collects all findings in one pass (severity, code, XML path). It parses the SRDF, resolves the linked URDF via `<tcad:urdf path="..."/>`, and cross-validates: robot-name match, group/joint/link/subgroup name existence, chain path resolvability, subgroup cycles, virtual/passive joints, end-effector topology, group-state membership/limits/completeness, disabled-collision pairs (including Adjacent-reason truthfulness), and misspelled elements. `--strict` treats warnings as failures; `--format json` emits a machine-readable findings document. It exits nonzero if any target fails. Relative targets resolve from the current working directory.

## Hard rules

- SRDF must reference an existing valid URDF through `<tcad:urdf path="..."/>` with a relative POSIX path.
- The SRDF robot name must match the URDF robot name.
- Every link, joint, group, and subgroup name must come from the URDF table or a group defined in the same file.
- Group states use URDF-native units: radians for revolute/continuous, meters for prismatic.
- Disabled collision pairs require truthful reasons and provenance.
- End-effector groups should not share links with their parent planning group.
- `$cad-viewer` owns optional local `moveit2_server` guidance for interactive planning review.
- Visual rendering review is useful but cannot prove planning correctness.

## References

- Authoring contract (structure, tcad:urdf link, golden skeleton): `references/authoring-contract.md`
- SRDF workflow (URDF table extraction, edit loop): `references/srdf-workflow.md`
- Planning ledger: `references/planning-ledger.md`
- Validation and verification recipe: `references/validation.md`
- End effectors: `references/end-effectors.md`
- Disabled collisions: `references/disabled-collisions.md`

For local MoveIt2 controls, use `$cad-viewer`; in that skill, read `references/moveit2-server.md`.
