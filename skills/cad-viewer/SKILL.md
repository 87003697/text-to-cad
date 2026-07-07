---
name: cad-viewer
description: Start CAD Viewer and return review links for explicit CAD, implicit CAD, robot-description, and G-code files. Use when visually reviewing `.step`, `.stp`, `.implicit.js`, `.implicit.mjs`, `.glb`, `.stl`, `.3mf`, `.gcode`, `.dxf`, `.urdf`, `.srdf`, or `.sdf` files, especially when handed off from CAD, implicit-cad, G-code, URDF, SRDF, or SDF generation skills.
---

# CAD Viewer

Provenance: maintained in [earthtojake/text-to-cad](https://github.com/earthtojake/text-to-cad).
Use the installed local skill files as the runtime source of truth; the
repository link is only for provenance and release review. If the user asks to
modify, debug, or iterate on CAD Viewer source itself, clone that repository and
work there — this installed skill runtime runs the Viewer, it is not where you
edit it.

Use this skill to open existing or newly generated CAD, implicit CAD,
robot-description, DXF, or plain FDM G-code files in CAD Viewer and hand back
live review links. The expected input is one or more explicit file paths.

## Start Viewer

Start one local CAD Viewer with `npm run start`, passing the absolute artifact
directory as `--dir`. This serves the prebuilt Viewer bundle plus the CAD API on
a single fixed port (`3245`). Use the Viewer URL it prints as-is, then add a
`file=` query value for each artifact you want to review.

> The default port `3245` is `0xCAD` — "CAD" in hexadecimal.

Choose `--dir` as the absolute directory that contains the model artifacts and
sidecars, commonly `<repo>/models` or the consuming project's equivalent model
directory. The `file=` value must be relative to that `--dir`.

Run from this skill directory:

```bash
npm --prefix scripts/viewer run start -- --host 127.0.0.1 --dir <absolute-model-root>
```

Use the printed Viewer URL and append `file=`:

```bash
http://127.0.0.1:3245/?dir=/absolute/project/models&file=path/to/model.step
```

One running Viewer serves any file (and any directory) — to review more
artifacts, reuse its URL with the appropriate `file=` (and a new `dir=` for a
different directory) rather than starting a second Viewer.

If port `3245` is already in use, the launcher exits with an error rather than
rolling to another port; rerun with an explicit free port, `--port <n>`, and use
the URL it prints. In sandboxed agent environments, local binding failures such
as `EPERM`/`EACCES` can be expected; rerun with the needed permission/escalation.

Add `--json` to also print a machine-readable result as the last stdout line
beginning with `{` (`{"url": ..., "port": ..., "action": "start"}`).

## Links

- Before returning any `file=` link, resolve `<dir>/<file>` and confirm the
  artifact exists. Pass the generated artifact (e.g. `.step`), not its
  generator source (e.g. `.py`). If the resolved path is missing, do not
  return the link, and instead report the problem and point to the correct
  generated artifact path.
- Return one Viewer URL per requested file.
- Start the Viewer once per absolute directory `--dir`, then append
  `file=<path>` for each requested file. The file path must be relative to
  `--dir`.
- For directory-only review links, return the URL printed by `start`
  without adding `file=`.
- Do not stop an existing Viewer server unless the user asks.
- If Viewer startup fails, report the failure and continue with the owning skill's non-GUI validation or artifacts.

## References

- Read `references/viewer-features.md` when you need supported file types, Viewer controls, or file-specific feature details.
- Read `references/moveit2-server.md` only when the user specifically needs optional SRDF MoveIt2 IK or path-planning controls.
