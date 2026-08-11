# Demo Models

Curated model fixtures and generator assets for text-to-cad workflows.

This tree is intended to be committed with Git LFS for large CAD, mesh, robot,
and G-code artifacts. Source generators and concise documentation remain normal
text files.

## Directory Map

- [benchmarks/](benchmarks/README.md): build123d benchmark generators plus STEP
  and mesh outputs.
- [fun/](fun/README.md): standalone generated CAD examples and printable
  outputs.
- [gcode/](gcode/README.md): small slicer input/output fixtures.
- [implicits/](implicits/README.md): browser-native implicit CAD examples.
- [mechanisms/](mechanisms/README.md): flattened mechanism STEP demos and
  generated render sidecars.
- `mesh-fixtures/`: durable OBJ and PLY inputs for mesh-processing and
  meshscope tests.
- [robots/](robots/README.md): URDF/SRDF robot fixtures, meshes, printable
  outputs, and selected robot STEP sources.
- [simple/](simple/README.md): compact build123d generators and STEP outputs
  for basic parts.

The larger `mechbench/` and `mechbench2/` external datasets are intentionally
not included in this committed fixture tree.

## Git LFS Fetching

Repository LFS config excludes `models/**` from default LFS fetches so ordinary
checkout and publish jobs can avoid downloading every model blob. Fetch the
model artifacts explicitly when you need local bytes:

```bash
git lfs pull --include="models/**" --exclude=""
```

## File Policy

This tree accepts CAD and robot-description **sources**, durable 3D and
fabrication **outputs** generated from them, dedicated mesh-processing
**fixtures**, and the **docs** that describe those artifacts. Nothing else
belongs here. Review material and scratch output live outside `models/`.

`tests/python/global/test_models_directory_policy.py` enforces the list below
against every tracked file. Adding a new artifact type is a deliberate policy
change: update this section and that test together.

### Allowed File Types

| Kind | Files | Storage |
| --- | --- | --- |
| Generator and helper source | `*.py` | normal Git |
| Implicit CAD source | `*.implicit.js`, `*.implicit.mjs` | normal Git |
| Robot description source | `*.urdf`, `*.srdf`, `*.sdf` | normal Git |
| Documentation | `*.md` | normal Git |
| CAD Viewer interaction sidecar | `.<stem>.step.js` | normal Git |
| CAD exchange output | `*.step`, `*.stp` | Git LFS |
| Mesh output | `*.stl`, `*.3mf`, `*.glb` | Git LFS |
| Mesh-processing fixture under `models/mesh-fixtures/` | `*.obj`, `*.ply` | Git LFS |
| CAD Viewer render/selector sidecar | `.<stem>.step.glb`, `.<stem>.stp.glb` | Git LFS |
| 2D output | `*.dxf` | Git LFS |
| Fabrication output | `*.gcode` | Git LFS |

The normal output rows cover the model formats CAD Viewer catalogs
(`SOURCE_EXTENSIONS` in `viewer/src/server/catalog/cadDirectoryScanner.mjs`).
OBJ and PLY are additionally allowed only under `models/mesh-fixtures/` as
durable inputs for mesh-processing and meshscope tests; CAD Viewer does not
currently catalog them as direct source files.

Hidden files are only CAD Viewer sidecars. `.<stem>.step.glb`,
`.<stem>.stp.glb`, and `.<stem>.step.js` must sit beside the `.step` or `.stp`
file they belong to.

Commit a generated output only when it is durable—something a workflow, test,
catalog entry, or print job depends on. Regenerable one-offs stay local.

### Not Allowed

Anything outside the table above, and in particular:

- **Review media** — `*.png`, `*.jpg`, `*.jpeg`, `*.gif`, `*.webp`, `*.avif`,
  `*.bmp`, `*.tif`, `*.tiff`, `*.svg`, `*.mp4`, `*.mov`, `*.webm`, `*.mkv`.
  Snapshots, orbit GIFs, and screen captures are review output, not model
  artifacts. Render them under `/tmp` and attach them to the conversation or
  pull request. Repository `.gitignore` rules keep a stray render from showing
  up as untracked under `models/`; the policy test is the gate that a forced
  `git add` still has to pass.
- **Data and metadata dumps** — `*.json`, `*.yaml`, `*.yml`, `*.csv`, `*.tsv`,
  `*.txt`, `*.toml`, `*.ini`, `*.xml`. Keep model parameters and metadata in
  generator source, or regenerate the sidecar locally as a transient artifact.
- **Archives and foreign CAD sources** — `*.zip`, `*.7z`, `*.tar`, `*.tgz`,
  `*.gz`, `*.rar`, `*.f3d`, `*.ipt`, `*.iam`, `*.sldprt`, `*.sldasm`, `*.prt`,
  `*.blend`, `*.scad`. Commit the generator and its exported STEP instead of
  the upstream bundle it came from.
- **Runtime debris** — `.DS_Store`, `__pycache__/`, `.cache/`, `*.log`,
  `*.lock`, `*.tmp`, and one-off timestamped review captures. Put temporary
  scratch artifacts under ignored local paths, not in this tree.
