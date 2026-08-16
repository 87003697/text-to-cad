# Meshscope Development candidate evidence

These records prove a provider-free local candidate only. They do not admit the
builder image, the three third-party runtime wheels, or the resulting project
wheel for production use.

The candidate was built with network disabled and pulling forbidden from exact
local builder image ID
`sha256:49de767070e9a205a5424860162e409c8ff4268e0567effb8d9265fc553a1ee2`.
Two ordinary builds and one alternate-absolute-root build produced the same
wheel bytes. `wheel-audit.json` records the exact wheel/RECORD and ELF closure;
`cup-native-conformance.json` records offline installation and a real depth-8
native measurement of `cup_cup_033`.

`candidate.json` keeps `admission.admitted=false`. SAI-003 must not be closed
until SAI-004 supplies final admission bindings for that exact builder and the
exact NumPy, Pillow, and trimesh inputs recorded here.
