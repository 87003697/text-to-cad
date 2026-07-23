---
name: mesh-inspect
description: Analyze a 3D mesh file with numeric statistics, a multi-view preview, and an interactive 3D handoff.
---

# Mesh inspection

## Purpose

Analyze a 3D mesh file across three modalities: numeric statistics
(JSON), a multi-view preview render (PNG), and a 3D interactive
handoff via `$cad-viewer`. See `references/mesh-analysis.md` for
field interpretation.

## Use this skill when

Use this skill when the user provides a 3D mesh file (`.ply`, `.obj`,
`.stl`, `.glb`) and needs to inspect its geometric properties — as
input to routing, dataset curation, quality control, or general
inspection.

## Tools and paths

From the mesh-inspect skill directory:

```bash
# Numeric: geometric statistics → JSON on stdout
python scripts/mesh-inspect <mesh-path>

# Preview: multi-view render → PNG on disk
python scripts/mesh-preview <mesh-path> --output <png>
```

Use the active project Python interpreter; treat `python` as an
interpreter placeholder, and use `--help` for the full interface
(including `--output <path>.json` for the stats CLI to write instead
of stdout).

## Required workflow

1. **Compute statistics.** Run `mesh-inspect <mesh>` →
   `${EXP_DIR}/mesh_stats.json`.
2. **Generate preview render.** Run `mesh-preview <mesh>` →
   `${EXP_DIR}/mesh_preview.png` (multi-view canvas).
3. **Hand off to `$cad-viewer`.** If the input mesh is `.stl` or
   `.glb`, hand the path directly. If `.ply` or `.obj`, first export
   a `.glb` sidecar (via `trimesh`) to `${EXP_DIR}/input_preview.glb`
   and hand that path instead. Skip cleanly if `$cad-viewer` is
   unavailable.

## Handoff

Return three artifacts to the caller:
- **Text**: `mesh_stats.json` path (or parsed JSON).
- **Image**: `mesh_preview.png` path (multi-view render).
- **3D**: `$cad-viewer` live link when installed; include the
  `.glb` sidecar path used for viewer handoff, when generated.

Include all three (or explicitly note which are unavailable) in the
final response.

## Non-negotiables

- Emit only valid JSON to stdout from `mesh-inspect`; write log/debug
  messages to stderr or omit them entirely.
- When handing off to `$cad-viewer`, ensure the file is in a
  supported format (`.stl` / `.glb`); export a `.glb` sidecar for
  `.ply` / `.obj` inputs.

## Progressive references

- `references/mesh-analysis.md` — field-by-field interpretation of the
  mesh-inspect JSON output.
