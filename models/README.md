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

## Cleanup Policy

- Keep canonical sources (`*.py`, `*.implicit.js`, `*.urdf`, `*.srdf`, and docs)
  readable in normal Git.
- Keep durable generated fixtures (`*.step`, `*.stl`, `*.3mf`, `*.glb`,
  `*.gcode`, and `*.dxf`) in Git LFS.
- Do not commit supplementary media or sidecar metadata such as `*.png`,
  `*.mp4`, `*.gif`, or `*.json` unless a future workflow defines them as a
  required model artifact.
- Do not commit local runtime debris such as `.DS_Store`, `__pycache__/`,
  `.cache/`, logs, or one-off timestamped review snapshots.
- Put temporary scratch artifacts under ignored local paths, not in this tree.
