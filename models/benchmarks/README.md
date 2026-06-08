# Benchmark Models

Benchmark CAD fixtures for repeatable geometry generation, import/export, and
viewer behavior checks. Each benchmark is intentionally more structured than the
small examples in `../simple/` and is meant to exercise a distinct modeling
surface.

## Contents

- `benchmark_*.py`: build123d generator source for each benchmark.
- `benchmark_*.step`: canonical generated STEP output for each benchmark.
- `.benchmark_*.step.glb`: generated render/selector sidecar paired with the
  STEP file.
- `benchmark_common.py`: shared helper functions for benchmark generators.
- `validate_benchmark.py`: local benchmark validation helper.

## Coverage

The benchmark set covers calibration blocks, flanges, brackets, shafts,
enclosures, clevises, engine-like fins, impellers, spiral stairs, and planetary
gear stages.

Keep benchmark examples here when they are intended as regression fixtures or
represent a richer version of a simpler shape.
