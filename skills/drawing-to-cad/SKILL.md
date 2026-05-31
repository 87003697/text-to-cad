---
name: drawing-to-cad
description: Generate STEP/STP CAD models from technical drawing sources such as images, PDFs, scanned drawings, NIST PMI test drawings, dimensioned orthographic/isometric drawings, GD&T/PMI callout sheets, and sketches with manufacturing dimensions. Use when Codex needs to extract drawing features, datums, dimensions, tolerances, and AP242-friendly PMI intent, author a build123d Python model, generate a nominal STEP file, validate the result against the drawing, and hand generated CAD artifacts to CAD Viewer.
---

# Drawing To CAD

## Operating Principle

Treat the drawing as a specification, not as trace art. First extract a semantic drawing spec with feature IDs, datums, dimensions, tolerances, and callout targets. Then build a parametric nominal CAD model from that spec and keep the PMI/drawing intent in a sidecar that can later map to STEP AP242 concepts.

Generate the solid at nominal geometry. Do not bake tolerance extremes into the shape unless the user explicitly asks for a limit model. Preserve tolerances, datum reference frames, notes, and unresolved assumptions as metadata.

LLMs are weak at dense drawing vision, perspective inversion, small annotation reading, and spatial bookkeeping. This skill must therefore use evidence gates: no design-critical geometry may be created from a visual impression alone. A feature is model-ready only when it has a drawing evidence record, a source view or crop, a size/location constraint or an explicit user-approved assumption, and a validation check that can fail.

## Default Outputs

Write durable outputs under the user's requested project artifact directory. If the host repo has an artifact-location policy, follow it without hardcoding that path into this skill.

- `model.py`: build123d source with named parameters and `gen_step()`.
- `model.step`: generated nominal STEP file.
- `model.pmi.json`: semantic drawing/PMI sidecar.
- `model.validation.json` or brief text report when useful.

Use millimeters unless the drawing clearly states otherwise.

## CAD Viewer Handoff

After completing drawing-to-CAD work that creates or modifies `.step`, `.stp`, `.stl`, `.3mf`, `.dxf`, or native `.glb` artifacts, hand the explicit file path(s) to `$cad-viewer` when that skill is installed. `$cad-viewer` owns starting or reusing CAD Viewer and returning link(s) to the relevant created or updated file(s); include those live viewer link(s) in the final response and validation report.

If the workflow produces only an extraction/PMI package because the drawing is underconstrained, state that no CAD Viewer handoff applies. If `$cad-viewer` is unavailable or startup fails, report that instead of silently omitting the handoff.

Viewer review is a human-facing check, not proof that the drawing has been matched. Use it to catch missing features, orientation errors, and obvious topology problems, then tie any finding back to source evidence and numeric validation.

## Workflow

1. Load `references/vision-and-reconstruction-limits.md` and classify the input before modeling. Mark whether it has vector geometry, orthographic views, calibrated scale, readable dimensions, and enough views to determine 3D shape.
2. Inspect the drawing source. Use image/PDF viewing tools as needed and record sheet title, revision, units, projection, view orientation, and drawing quality.
3. Run a detail pass. Models often miss small marks in dense drawings, so inspect high-resolution crops/tiles for every callout cluster, feature control frame, datum tag, leader endpoint, tiny tolerance, and note. Do not declare a symbol absent unless the relevant crop was inspected.
4. Build a semantic spec. Load `references/extraction-schema.md` and capture datums, feature IDs, dimensions, GD&T, notes, source coordinates, confidence, and open questions.
5. Build an evidence ledger before modeling. Every feature must have source evidence, a confidence score, a target view/crop, and either explicit dimensions, derived constraints, or a listed assumption. Mark model-readiness for each feature.
6. Decide whether the drawing is sufficiently constrained. If missing dimensions or projection ambiguity block a unique model, ask a focused question or produce an extraction/PMI package without claiming a precise STEP. Do not turn a single raster pictorial into a precise model from visible proportions alone.
7. Author `model.py` only for model-ready geometry. Load `references/modeling-workflow.md`; create named parameters, construction planes, datum faces, and feature labels that match the sidecar IDs.
8. Generate STEP from this skill directory with this skill's launcher:

```bash
python scripts/step "$MODEL_SOURCE"
```

Use `SOURCE.py=OUTPUT.step` or `--output` when the requested filename differs from the source stem.

9. Validate the generated STEP against the extracted spec from this skill directory:

```bash
python scripts/inspect refs "$STEP_OUTPUT" --facts --planes --positioning
```

Run targeted measurements or small Python inspectors for exact hole diameters, slot lengths, angles, datum face locations, and declared symmetry. Load `references/validation.md` for the validation checklist. For image sources, include an overlay or view-comparison check whenever a comparable view can be rendered.

10. Hand generated or modified CAD artifacts to `$cad-viewer` as described above when available. If no supported CAD artifact was produced, record why the handoff is not applicable.
11. Iterate on source, not on the generated STEP. If a check fails, update the extraction, `model.py`, or `model.pmi.json`, regenerate, and rerun the relevant check.
12. Report generated files, returned `$cad-viewer` links or handoff failure/not-applicable status, assumptions, unresolved ambiguities, validation actually run, and any dimensions or GD&T that remain metadata-only. If the output is approximate, say so in the filename/report and do not present it as a precise reproduction.

## Extraction Rules

Separate these concepts:

- Shape features: holes, slots, bosses, pockets, edge breaks, outside profiles, datum surfaces, axes, and patterns.
- Size/location dimensions: nominal values, limit values, plus/minus tolerances, baseline/chain relationships, and target features.
- GD&T/PMI: geometric characteristic, tolerance zone, modifiers, datum reference frame, target feature, and any "all around" or pattern scope.
- Presentation: leader geometry, original text, drawing coordinates, view association, and confidence.

Prefer AP242-friendly names such as `shape_feature`, `datum_feature`, `datum_reference_frame`, `dimensional_size`, `geometric_tolerance`, `annotation_occurrence`, and `presentation_target`. The sidecar does not need to be valid STEP; it should be easy to map into AP242 later.

## Modeling Rules

Model the part from stable datums outward:

- Pick datum A/B/C or equivalent drawing bases as source construction references.
- Use parameters named from drawing intent, not screen positions.
- Build repeated holes/slots as explicit patterns with shared metadata only when the drawing evidence supports the pattern.
- Keep feature IDs stable between `model.py` and `model.pmi.json`.
- Represent threads, surface finish, material, and manufacturing notes as metadata unless geometry is required.

For drawings with only one pictorial view, do not infer a precise 3D footprint from the rendered appearance. Extract visible dimensions and PMI, model only geometry constrained by evidence, and ask for an orthographic drawing, vector PDF, original CAD, or user approval before filling unconstrained footprints, hidden features, or symmetry.

## Tool Environment

Use Python 3.11 or newer. Install dependencies for this skill from its own requirements file when needed:

```bash
python -m pip install -r requirements.txt
```

Run these commands from this skill directory. The bundled launchers are copied from the CAD toolchain but are local to this skill. Do not import code from sibling skill directories or use repo-rooted paths to this skill's files.

## References

- `references/extraction-schema.md`: AP242-aligned semantic drawing spec and sidecar shape.
- `references/vision-and-reconstruction-limits.md`: LLM bottlenecks, evidence gates, and stop conditions.
- `references/modeling-workflow.md`: converting the spec into build123d source.
- `references/validation.md`: checks to prove the STEP matches the drawing.
