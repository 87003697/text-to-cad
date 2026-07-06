# Fun Models

Standalone generated CAD examples used for demos, visual inspection, and
occasional print/export experiments. These models are more expressive than the
benchmark fixtures and are not expected to form a systematic test suite.

## Contents

- `*.step.py`: build123d generator source for authored models.
- `*.step`: canonical generated STEP output.
- `*.step.glb/`: generated render/selector package paired with each STEP file;
  per-folder content-addressed render content lives under `__cadgen__`.
- `<name>.params.js`: optional JS parameter/animation sidecar, declared through
  the model's `gen_step()` envelope (`{"shape": ..., "params":
  "<name>.params.js"}`) and recorded as `paramsPath` in the package descriptor.
- `*.stl`, `*.3mf`, and direct `*.glb`: durable exported/printable artifacts
  when the export itself is useful as a fixture.

Avoid adding screenshots, videos, throwaway slicer profiles, or timestamped
review captures here.

## Subdirectories

SpaceX reconstruction packages moved to [../spacex/](../spacex/README.md).
