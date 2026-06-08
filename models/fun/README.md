# Fun Models

Standalone generated CAD examples used for demos, visual inspection, and
occasional print/export experiments. These models are more expressive than the
benchmark fixtures and are not expected to form a systematic test suite.

## Contents

- `*.py`: build123d generator source for authored models.
- `*.step`: canonical generated STEP output.
- `.*.step.glb`: generated render/selector sidecars paired with STEP files.
- `.*.step.js`: optional interaction sidecars paired with STEP files.
- `*.stl`, `*.3mf`, and direct `*.glb`: durable exported/printable artifacts
  when the export itself is useful as a fixture.

Avoid adding screenshots, videos, throwaway slicer profiles, or timestamped
review captures here.
