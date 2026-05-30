# Inspection and validation

Read this file for every generated STEP artifact and whenever the user asks for geometry facts, references, dimensions, mating, diffing, or frame inspection.

## Contents

- Principle
- Tool
- Relationship to build123d joints
- Validation hierarchy
- Reference discovery
- Measurement checks
- Mating checks
- Interference detection
- Frame inspection
- Diff checks
- CAD Viewer handoff
- Validation report content

## Principle

Use programmatic geometry checks as the validation source of truth. Use CAD Viewer links and CAD `scripts/snapshot` outputs for visual review, not as substitutes for measurements, facts, planes, labels, or positioning checks.

## Tool

The launcher lives in the CAD skill directory:

```bash
python scripts/inspect {refs|diff|frame|measure|mate|worker|batch} ...
```

Inspection targets are resolved from the command cwd unless absolute. Keep the root model in `SKILL.md` explicit when choosing whether to run from the workspace root or the skill directory.

Common data-output flags on inspection commands:

- `--format json|text`; default is machine-readable output.
- `--quiet`
- `--verbose`

Accepted target forms:

```text
@cad[path/to/entry#selector]
path/to/entry
path/to/entry.step
```

## Relationship to build123d joints

If the source uses build123d `Joint` objects, validate the generated STEP exactly as you would validate explicit `Location` placements. Source-level joints express and compute placement during generation; CLI `inspect mate` verifies the exported result by returning a translation delta between selected references. Do not confuse CLI `mate` with build123d `Joint.connect_to()`. Use `positioning.md` for authoritative source-authoring rules.

## Validation hierarchy

Default validation sequence:

1. Generation completed and the STEP/STP file exists.
2. `refs --facts --planes --positioning` confirms scale, labels, major planes, and placement-ready references.
3. `measure` confirms critical dimensions and offsets.
4. `mate` confirms read-only alignment deltas for assembly interfaces or ref-to-ref positioning; it does not create source-level build123d joints.
5. `scripts/interference` runs on-demand solid interference checks when unexpected assembly collisions are plausible or the user asks for collision detection. Use explicit scope, pair, body-depth, collapse, and allow-pair controls instead of making interference a generation side effect.
6. `frame` confirms world frame for occurrences or selected references.
7. `diff` compares before/after geometry for modifications.
8. Created or modified supported artifacts are handed to `$cad-viewer` for live viewer links when available.
9. Saved CAD `scripts/snapshot` packets are ALWAYS run for visible created or updated primary STEP/STP artifacts unless `snapshot-review.md` documents that no visible geometry changed or no valid artifact exists; when run, every visual concern is followed by a deterministic geometry check before it becomes a validation claim.

## Reference discovery

Compact facts and planes:

```bash
python scripts/inspect refs path/to/model.step \
  --facts --planes --positioning
```

Detailed selector inspection:

```bash
python scripts/inspect refs '@cad[path/to/model.step#selector]' \
  --detail --positioning
```

Topology enumeration, only when needed:

```bash
python scripts/inspect refs path/to/model.step --topology
```

Plane options:

```bash
--plane-coordinate-tolerance FLOAT
--plane-min-area-ratio FLOAT
--plane-limit INT
```

Use lower plane limits and compact facts for normal validation. Use topology enumeration only for selector discovery, complex debugging, or when a feature cannot be verified through facts/planes/measurements.

## Measurement checks

Use `measure` for bounding distances, clearances, offsets, part spacing, plate thickness, hole-to-face distances, and alignment verification.

```bash
python scripts/inspect measure \
  --from '@cad[path/to/model.step#selector_a]' \
  --to '@cad[path/to/model.step#selector_b]' \
  --axis x
```

Axis may be inferred when possible, but specify `x`, `y`, or `z` for deterministic checks.

## Mating checks

Use CLI `mate` when two exported STEP references should be flush or centered. It returns a read-only translation delta; it does not edit source files and does not replace native build123d joints in source. When source uses build123d `Joint`/`connect_to()` placement, still validate the resulting exported geometry with `refs --positioning`, `frame`, `measure`, or CLI `mate`.

```bash
python scripts/inspect mate \
  --moving '@cad[path/to/assembly.step#moving_selector]' \
  --target '@cad[path/to/assembly.step#target_selector]' \
  --mode flush \
  --axis z
```

Apply any required correction in the Python source using build123d joint definitions, `.connect_to()` calls, `Location`, parameter changes, or assembly child placement. Regenerate and re-inspect.

## Interference detection

Run standalone interference detection when an assembly may contain unexpected solid overlaps. The tool is agent-oriented: JSON is the default, all collisions are treated as violations unless an `--allow-pair` rule says otherwise, and scope controls are explicit.

```bash
python scripts/interference path/to/assembly.step --pretty
python scripts/interference path/to/assembly.step --body-depth 2 --fail-on-interference
python scripts/interference path/to/assembly.step --set-a 'o1.3.*' --set-b 'o1.4.*' --pretty
python scripts/interference path/to/assembly.step --pair base_plate:triangular_prism --include-separated --pretty
python scripts/interference path/to/assembly.step --collapse '*servo*' --allow-pair press_fit_pin:socket --pretty
```

Use `--body-depth` or `--collapse` to avoid spending time inside trusted rigid/vendor subassemblies. Use `--set-a`/`--set-b` for cross-set checks, `--pair` for a targeted drilldown, `--clearance` plus `--include-clearance` for near-miss checks, and `--include-allowed` when auditing intentional interferences. Interference detection is intentionally on demand rather than a `scripts/step` generation side effect.

## Frame inspection

Use `frame` to validate occurrence transforms and selected-reference world frames:

```bash
python scripts/inspect frame '@cad[path/to/model.step#selector]'
```

Frame output is useful for assemblies, part-local-to-world conversion, and placement debugging.

## Diff checks

For modification tasks, compare before and after artifacts:

```bash
python scripts/inspect diff path/to/before.step path/to/after.step --planes
```

Use diff when a repair, feature addition, or source edit could affect unrelated geometry.

## CAD Viewer handoff

For every final response involving a generated or modified supported artifact (`.step`, `.stp`, `.stl`, `.3mf`, `.dxf`, or native `.glb`), hand off the explicit artifact path to `$cad-viewer` when available and return the link it prints. If an important selector was inspected, return the textual `@cad[...]` reference beside the owning CAD Viewer link.

Use `snapshot-review.md` to choose packet size and documented skip cases after deterministic checks. For visible created or updated primary STEP/STP artifacts, ALWAYS prefer CAD `scripts/snapshot` over manual viewer or Playwright inspection for visual feedback. Viewer handoff alone does not count as saved snapshot review.

## Validation report content

Report only checks that were actually run or directly supported by tool output.

Use this structure:

```text
Validation:
- STEP generation: passed/partial/failed
- Solids/assembly: <counts and labels>
- Bounding box: <dimensions and units>
- Major planes/refs: <summary>
- Positioning: <frame/measure/mate results if relevant>
- Feature checks: <holes, cutouts, bosses, etc.>
- Visual review: `$cad-viewer` viewer link returned; CAD `scripts/snapshot` PNG/GIF included or skipped with reason; follow-up geometry checks for any visual findings
```

Do not claim:

- structural safety
- process certification
- tolerance compliance
- manufacturability beyond geometric plausibility
unless the relevant analysis or manufacturing data was explicitly performed.
