# AGENTS.md

This repository is a harness for script-driven CAD and robot-description
generation with coding agents.

`AGENTS.md` is for repository-level operating rules only. Domain workflows,
validation rules, Explorer/link behavior, rendering policy, CLI details,
dependency setup, and generator contracts live in the relevant agent skills and
their references.

Do not duplicate reusable skill methodology in this file. Skill location and
packaging may vary by agent; rely on the active agent's skill-discovery
mechanism.

If you are modifying CAD Explorer itself, use the CAD Explorer documentation
provided with the CAD Explorer skill or local project documentation.

## Harness context

Project files are repo-relative. This harness does not reserve a project-file
directory. Project entries may live at the repository root under folders such as
`STEP/`, `STL/`, `DXF/`, and `3MF/`, or in another explicit repo-relative
layout chosen by the project.

Skill tools are file-targeted. They do not depend on a harness layout and do not
prepend a project root.

Project-specific context may live in compact root-level notes such as
`PROJECT.md`. Do not copy reusable skill workflow rules, validation policy,
Explorer/link rules, image-review policy, generator contracts, or full CLI
syntax into project notes; refer to the relevant skill reference instead.

## Python environment

Prefer the repo-local CAD runtime when it exists:

```bash
./.venv/bin/python
```

If `.venv` is missing or cannot import required CAD runtime modules, create or
install the environment from the repo root using the dependency instructions
provided by the relevant CAD skill.

Other bundled workflows own their own dependency setup. Install those
dependencies only when using those workflows.

## Source of truth

Generated CAD files, URDF, SDF, and SRDF files, Explorer sidecars, renders,
topology, meshes, and flat-pattern artifacts are derived artifacts.

Do not hand-edit derived artifacts unless explicitly instructed. Edit the owning
source file or imported source file first, then regenerate the explicit target
with the relevant skill tool.

If regenerated output differs from checked-in generated files, the regenerated
output is authoritative.

## Repo policies

Keep project files in explicit repo-relative locations.

Use explicit generation targets. Do not run directory-wide generation.

Generation tools write and overwrite configured outputs. They do not delete
stale outputs when paths change.

Update project-local documentation only when project focus, entry roles,
inventory, dependency notes, durable quirks, or preferred rebuild roots change.

CAD outputs are often LFS-tracked. Prefer path-limited `git status` during CAD
work, especially while generated files are changing.

For bookkeeping-only full status, use:

```bash
git -c filter.lfs.clean= \
    -c filter.lfs.smudge= \
    -c filter.lfs.process= \
    -c filter.lfs.required=false \
    status --short
```

Never disable LFS filters for `git add`, commits, or other object-writing
operations.

## Execution notes

Start with the narrowest source-only search that can identify directly affected
files.

Exclude generated artifacts, binary CAD files, caches, and build outputs from
default searches unless the task explicitly targets them.

If the first pass makes scope clear, edit the source first and validate after.

Do not run mutable generation, inspection, and render/review steps in parallel
against geometry that is still changing in the same edit loop. Rebuild first,
then inspect, then review.

In cloud or constrained environments, avoid full-repo hydration when affected
entries are known. Fetch only the needed inputs, generated outputs, and LFS
objects for the entries being edited and explicitly regenerated.
