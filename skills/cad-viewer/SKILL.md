---
name: cad-viewer
description: Start or reuse CAD Viewer and return review links for explicit CAD, implicit CAD, robot-description, and G-code files. Use when visually reviewing `.step`, `.stp`, `.implicit.js`, `.implicit.mjs`, `.glb`, `.stl`, `.3mf`, `.gcode`, `.dxf`, `.urdf`, `.srdf`, or `.sdf` files, especially when handed off from CAD, implicit-cad, G-code, URDF, SRDF, or SDF generation skills.
---

# CAD Viewer

Provenance: maintained in [earthtojake/text-to-cad](https://github.com/earthtojake/text-to-cad).
Use the installed local skill files as the runtime source of truth; the
repository link is only for provenance and release review.

Use this skill to open existing or newly generated CAD, implicit CAD,
robot-description, DXF, or plain FDM G-code files in CAD Viewer and hand back
live review links. The expected input is one or more explicit file paths.

## Start Viewer

Start or reuse one local CAD Viewer with `npm run agent:start`, passing the
absolute artifact directory as `--dir`. The launcher targets a single fixed
port (`4178`): it reuses a compatible Viewer already running there (activating
the requested `--dir`), or starts one if the port is free. Use the Viewer URL it
prints as-is, then add only a `file=` query value for the artifact you want to
review.

Choose `--dir` as the absolute directory that contains the model
artifacts and sidecars, commonly `<repo>/models` or the consuming project's
equivalent model directory. The `file=` value must be relative to that `--dir`.
Do not rewrite `?dir=` or start a separate Viewer just to change directories —
rerun `agent:start` with the new `--dir` and it activates that directory on the
running Viewer.

Run from this skill directory:

```bash
npm --prefix scripts/viewer run agent:start -- --host 127.0.0.1 --dir <absolute-model-root>
```

Use the printed Viewer URL and append `file=`:

```bash
http://127.0.0.1:4178/?dir=/absolute/project/models&file=path/to/model.step
```

If port `4178` is already held by a non-Viewer process, the launcher does not
roll to another port — it exits with an error. Rerun with an explicit free port,
`--port <n>`, and use the URL it prints (which reflects the chosen port). In
sandboxed agent environments, local binding or probe failures such as `EPERM` or
`EACCES` can be expected; rerun the same command with the needed
permission/escalation.

Add `--json` to also print a machine-readable result as the last stdout line
beginning with `{` (`{"url": ..., "port": ..., "action": "reuse"|"start"}`).

## Links

- Before returning any `file=` link, resolve `<dir>/<file>` and confirm the
  artifact exists. Pass the generated artifact (e.g. `.step`), not its
  generator source (e.g. `.py`). If the resolved path is missing, do not
  return the link, and instead report the problem and point to the correct
  generated artifact path.
- Return one Viewer URL per requested file.
- Start/reuse the Viewer once per absolute directory `--dir`, then append
  `file=<path>` for each requested file. The file path must be relative to
  `--dir`.
- For directory-only review links, return the URL printed by `agent:start`
  without adding `file=`.
- Do not stop an existing Viewer server unless the user asks.
- If Viewer startup fails, report the failure and continue with the owning skill's non-GUI validation or artifacts.

## References

- Read `references/development.md` when the user asks to modify, debug, or
  iterate on CAD Viewer source.
- Read `references/viewer-features.md` when you need supported file types, Viewer controls, or file-specific feature details.
- Read `references/moveit2-server.md` only when the user specifically needs optional SRDF MoveIt2 IK or path-planning controls.
