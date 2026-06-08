# G-code Fixtures

Small FDM slicing fixtures used by the G-code skill and related validation
workflows. This folder keeps both slicer inputs and durable generated G-code
outputs for known simple parts.

## Contents

- `*_sample.stl`: mesh inputs used for slicing checks.
- `*_sample.gcode`: canonical generated G-code outputs for those inputs.

These files are intentionally sample-sized. Printer credentials, slicer
profiles, logs, and transient dry-run outputs should not be committed here.
