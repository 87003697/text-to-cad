---
name: cad-viewer
description: Start or reuse CAD Viewer and return review links for explicit CAD, robot-description, and G-code files. Use when visually reviewing `.step`, `.stp`, `.glb`, `.stl`, `.3mf`, `.gcode`, `.dxf`, `.urdf`, `.srdf`, or `.sdf` files, especially when handed off from CAD, G-code, URDF, SRDF, or SDF generation skills.
---

# CAD Viewer

Use this skill to open existing or newly generated CAD, robot-description, DXF, or plain FDM G-code files in CAD Viewer and hand back live review links. The expected input is one or more explicit file paths.

## Start Viewer

Run from this skill directory:

```bash
npm --prefix scripts/viewer run serve:ensure -- --file path/to/model.step
```

When the workspace is known, pass it explicitly:

```bash
npm --prefix scripts/viewer run serve:ensure -- \
  --workspace-root /path/to/workspace \
  --file path/to/model.step
```

Use only the URL printed by `serve:ensure`; do not assume a fixed port. `serve:ensure` probes registered local Viewer servers, reuses a matching scan root when possible, and otherwise starts the packaged production server. In sandboxed agent environments, local binding/probing failures such as `EPERM` or `EACCES` can be expected; rerun the same command with the needed permission/escalation instead of choosing an arbitrary port.

Pass exactly one `--file` per `serve:ensure` invocation. For multiple requested files, run the command once per file and return each printed URL.

## Links

- Return the printed Viewer link for each requested file.
- If a single server is already running for the same scan root, reuse it and vary the `?file=` query only when that points at the requested file.
- Do not stop an existing Viewer server unless the user asks.
- If Viewer startup fails, report the failure and continue with the owning skill's non-GUI validation or artifacts.

## References

- Read `references/development.md` when the user asks to modify, debug, or
  iterate on CAD Viewer source.
- Read `references/viewer-features.md` when you need supported file types, Viewer controls, or file-specific feature details.
- Read `references/moveit2-server.md` only when the user specifically needs optional SRDF MoveIt2 IK or path-planning controls.
