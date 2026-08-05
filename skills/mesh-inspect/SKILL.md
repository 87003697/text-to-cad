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

Use this skill when the user provides a 3D mesh file (`.ply`, `.obj`, `.3mf`,
`.stl`, `.glb`) and needs to inspect its geometric properties — as input to
routing, dataset curation, quality control, or general inspection.

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
   `${EXP_DIR}/mesh_preview.png` (multi-view canvas). For `.ply`, `.obj`,
   or `.3mf`, also pass `--glb-output
   ${EXP_DIR}/input_preview.glb`; this command performs the required CAD Z-up
   to glTF Y-up conversion and neutral preview-material normalization.
3. **Hand off to `$cad-viewer`.** If the input mesh is `.stl` or
   `.glb`, hand the original path directly. If it is `.ply`, `.obj`, or
   `.3mf`, hand off the exact `input_preview.glb` produced by step 2. Do not
   create a second GLB with an ad-hoc `trimesh.export`; it would lose the
   coordinate/material contract. Other formats are outside this skill's
   Viewer-handoff contract. Skip cleanly if `$cad-viewer` is unavailable.

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
- `mesh_preview.png` MUST be produced successfully by `scripts/mesh-preview`.
  If `mesh-preview` exits non-zero, stop the inspection step and report the
  failure. Do not substitute Matplotlib, point-cloud scatter, trimesh scene
  rendering, or any other renderer. `$cad-viewer` being optional applies only
  to the interactive handoff, not to `mesh_preview.png`.
- When handing off to `$cad-viewer`, ensure the file is in a
  supported format (`.stl` / `.glb`). For `.ply`, `.obj`, or `.3mf`, export
  the `.glb` sidecar only through `mesh-preview --glb-output`.
- Treat `mesh_stats.json` and the Viewer preview as the inspection evidence for
  dense inputs. Do not call `trimesh.split()` as an ad-hoc diagnostic when
  `face_count > 100000`; connected-component splitting can duplicate large
  topology arrays and OOM the pilot before modeling starts.

## Progressive references

- `references/mesh-analysis.md` — field-by-field interpretation of the
  mesh-inspect JSON output.
