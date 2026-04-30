<div align="center">

<img src="assets/urdf-demo.gif" alt="Demo of the URDF skill generating and previewing robot description output" width="100%">

<br>

</div>

# URDF Skill

Generated robot-description tools for coding agents.

The URDF skill operates on explicit Python sources that define `gen_urdf()`. It does not assume a project root or directory layout, so it can be used in any project that provides the required Python environment.

The demo GIF is tracked with Git LFS but skipped by default so normal clones stay small. To hydrate it locally in the standalone skill repo:

```bash
git lfs pull --include="assets/*.gif" --exclude=""
```

## What It Can Do

- Regenerate `.urdf` outputs from Python `gen_urdf()` sources.
- Validate URDF XML with `yourdfpy`.
- Check link names, joint names, parent/child references, rooted tree structure, and joint limits.
- Encourage complete physical links with `inertial`, `visual`, and `collision` tags.
- Validate visual and collision mesh filenames and supported mesh references.
- Print compact robot, link, and joint summaries after regeneration.
- Keep URDF generation separate from STEP, STL, GLB/topology, and DXF generation.

## Commands

Run commands from the project that owns the target files:

```bash
python <urdf-skill>/scripts/gen_urdf/cli.py path/to/robot.py
python <urdf-skill>/scripts/gen_urdf/cli.py path/to/robot.py --summary
```

Install the skill runtime dependencies from [requirements.txt](requirements.txt) when the active Python environment does not already provide them.

If URDF mesh references depend on changed geometry, regenerate those mesh outputs separately with the owning project's mesh workflow.

For agent-facing workflow rules, use [SKILL.md](SKILL.md).
