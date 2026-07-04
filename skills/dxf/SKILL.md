---
name: dxf
description: Generate, regenerate, and validate 2D DXF drawings from Python ezdxf sources. Use for DXF files, `.dxf.py` generators, gen_dxf() sources, 2D profiles, outlines, templates, gaskets, panels, flat patterns, laser/plasma/waterjet cut layouts, and 2D drawing exports of CAD geometry.
---

# DXF generation and validation

Provenance: maintained in [earthtojake/text-to-cad](https://github.com/earthtojake/text-to-cad).
Use the installed local skill files as the runtime source of truth; the
repository link is only for provenance and release review.

## Purpose

Create or modify 2D DXF drawings from natural-language requirements or from CAD geometry, generate validated drawing artifacts, and return checked outputs. A DXF drawing's source of truth is a dedicated Python generator file named `<name>.dxf.py` defining `gen_dxf()`; the CLI owns output paths.

The default build product is the **drawing package** — a render artifact the CAD Viewer serves and auto-regenerates:

```
<model-folder>/__cadcache__/models/<name>.dxf.py/
  drawing.json    # provenance + freshness descriptor
  drawing.dxf     # the built DXF
```

The sibling `<name>.dxf` file is written **on demand only** (`--dxf`, `-o`, or a `SOURCE=OUTPUT` pair) for deliverables handed to cutting services or other tools. Do not commit generated `.dxf` outputs; the package cache is gitignored and rebuilt on demand.

## The three DXF workflows

1. **DXF generated from scratch** (standalone drafting — gaskets, panels, templates, cut layouts with no 3D model behind them): a `<name>.dxf.py` that builds an `ezdxf` document directly.

   ```python
   import ezdxf

   def gen_dxf():
       document = ezdxf.new("R2010")
       # ... draw modelspace entities ...
       return {"document": document}
   ```

2. **DXF derived from a generated STEP part** (flat patterns / profiles of a `$cad` model): a `<name>.dxf.py` beside the `<name>.step.py` it projects. Generator entry files use dotted extensions and cannot be imported by module name, so reuse the STEP source's geometry by path-loading it:

   ```python
   from pathlib import Path
   from cadgen.sources import load_source_module

   _step = load_source_module(Path(__file__).with_name("bracket.step.py"))

   def gen_dxf():
       return {"document": _step.build_dxf()}
   ```

   Keep the shared drawing logic (e.g. a `build_dxf()` helper that unfolds the part) in the `.step.py` or a plain helper module; the `.dxf.py` is the drawing entry point. The loaded `.step.py` and its imports are recorded in the drawing's source closure, so editing the 3D part automatically invalidates the cached drawing.

3. **DXF derived from an imported STEP** (a `.step`/`.stp` file with no Python source): a `<name>.dxf.py` that reads the STEP (e.g. `build123d.import_step`) and projects it. Declare the STEP as an envelope `sources` dependency so replacing the file invalidates the cached drawing:

   ```python
   def gen_dxf():
       # ... project geometry loaded from vendor_part.step ...
       return {"document": document, "sources": ["vendor_part.step"]}
   ```

`gen_dxf()` must live in a dedicated `.dxf.py` file: a source defining both `gen_step()` and `gen_dxf()` is rejected. A plain `<name>.py` defining only `gen_dxf()` is still accepted as an explicit CLI target (the CLI is naming-agnostic), but only `.dxf.py` files are catalog entries the CAD Viewer lists and rebuilds.

## Use this skill when

Use this skill when the user asks for DXF files, 2D drawings, profiles, outlines, templates, gaskets, panels, flat patterns, or cut layouts for laser, plasma, waterjet, or CNC routing.

Use `$cad` for the 3D part or assembly a DXF derives from. Use `$sendcutsend` for SendCutSend-specific upload preflight.

## Defaults

Use these defaults unless the user specifies otherwise:

- Units: millimeters; set them explicitly on the document (`doc.units = ezdxf.units.MM`).
- Geometry lives in modelspace at 1:1 scale.
- Cut profiles are closed polylines or closed line/arc loops; open contours only for engraving or reference geometry.
- For CAD-backed parts, prefer deriving DXF cut contours from the actual STEP/solid topology: build or load the 3D shape, select/project the real planar faces, unfold them into flat coordinates, and emit closed contours from those projected face wires. Use hand-drawn parametric outlines only when there is no reliable 3D topology to project.
- Layers carry intent: keep cut geometry and bend/fold lines on separate layers, and include "bend" in bend-layer names so downstream tools classify them as bends rather than cuts.
- DXF layers are drawing structure, not STEP part/assembly structure.

## Tool

The launcher lives in the DXF skill directory:

```bash
python scripts/dxf targets... [flags]
```

Use the active project Python interpreter; treat `python` as an interpreter placeholder, and use `--help` for the full interface. Target paths resolve from the command's current working directory; run from the workspace that owns the artifacts with cwd-relative target paths. Keep a drawing generator in the same directory as the geometry it derives from, named `<name>.dxf.py`.

A DXF target is a Python source defining:

```python
def gen_dxf():
    ...
    return {"document": document}  # or a bare ezdxf document
    # optional: "sources": [...] file dependencies folded into freshness
```

Every run builds/refreshes the drawing package. Flags:

- `--dxf` — also write the sibling `<name>.dxf` export.
- `-o`/`--output PATH` — export to a custom path; only with one plain generated Python target.
- `SOURCE.dxf.py=OUTPUT.dxf` positional pairs — per-target custom export paths.
- `--force` — rebuild even when the cached drawing package is current (an unchanged source closure is otherwise skipped).

Do not put output paths in the `gen_dxf()` return value.

`scripts/dxf` is a generator; it does not inspect existing `.dxf` files. For existing DXF inspection, use `ezdxf` for entity/layer checks and `$cad-viewer` for visual review.

## Workflow

1. Convert the request into a short brief: outline dimensions, holes and slots, layers, units, output path, and validation targets.
2. Pick the workflow: standalone drafting, projection of a generated STEP (create and validate the STEP geometry with `$cad` first), or projection of an imported STEP (declare it in `sources`).
3. Write or edit the `<name>.dxf.py` source with meaningful dimensions as named parameters, reusing the STEP source's geometry helpers instead of duplicating formulas.
4. Run `scripts/dxf` on explicit Python source targets only; do not run directory-wide generation.

```bash
python scripts/dxf path/to/source.dxf.py
python scripts/dxf path/to/source.dxf.py --dxf
python scripts/dxf path/to/source.dxf.py -o path/to/output.dxf
python scripts/dxf path/to/a.dxf.py=out/a.dxf path/to/b.dxf.py=out/b.dxf
```

5. Validate the generated DXF deterministically, then hand off and report.

## Viewer integration

`<name>.dxf.py` files are CAD Viewer catalog entries, listed whether or not their drawing package has been built. Opening one triggers the unified render-artifact flow: a missing or stale package (any source-closure file — the generator, path-loaded STEP sources, helper modules, or declared `sources` files — newer than the descriptor) rebuilds automatically. Raw imported `.dxf` files render directly and are never rebuilt.

## Validation

Verify the generated drawing with targeted `ezdxf` checks instead of eyeballing: entity counts by type and layer, closed flags on cut profiles, drawing extents, and every dimension the user specified. Read the built DXF from the drawing package (or from the exported path when one was requested):

```python
import ezdxf

doc = ezdxf.readfile("path/to/__cadcache__/models/source.dxf.py/drawing.dxf")
msp = doc.modelspace()
profiles = [e for e in msp.query("LWPOLYLINE") if e.closed]
holes = msp.query('CIRCLE[layer=="0"]')
```

Report only checks that actually ran.

## Handoff

After creating or modifying DXF drawings, you must ALWAYS hand the explicit `.dxf.py` file path(s) to `$cad-viewer` when that skill is installed and include its live viewer link(s) in the final response. If `$cad-viewer` is unavailable or startup fails, report that and rely on `ezdxf` checks instead of silently omitting the handoff.

Final responses should include generated files, returned viewer links, validation actually run, and assumptions.
