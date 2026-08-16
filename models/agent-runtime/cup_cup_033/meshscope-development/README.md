# Meshscope Development candidate evidence

These records prove a provider-free local candidate only. They do not admit the
builder image, the three third-party runtime wheels, or the resulting project
wheel for production use.

Each build record embeds a closed host-launch receipt for the exact local
builder image ID
`sha256:49de767070e9a205a5424860162e409c8ff4268e0567effb8d9265fc553a1ee2`.
The receipts bind `--pull=never`, `--network=none`, linux/amd64, and three
unique invocations: two at `/work` and one at `/alternate-work`. All three
produced the same wheel bytes. `wheel-audit.json` records the exact
wheel/RECORD and ELF closure; `cup-native-conformance.json` records clean-env
offline installation and a real depth-8 native measurement of `cup_cup_033`.
That measurement used the explicit `no-provider-configured` path and records
zero provider dispatches; it does not claim an independently measured provider
or network-denial boundary.

`candidate.json` keeps `admission.admitted=false`. SAI-003 must not be closed
until SAI-004 supplies final admission bindings for that exact builder and the
exact NumPy, Pillow, and trimesh inputs recorded here.
