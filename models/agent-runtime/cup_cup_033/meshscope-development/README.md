# Meshscope Development candidate evidence

These records prove a provider-free, locally qualified Development candidate
only. They do not admit the builder image, the three third-party runtime
wheels, or the resulting project wheel for Formal/S3/production use.

Each build record embeds a closed host-launch receipt for the exact local
builder image ID
`sha256:9f53dae6dd44ad326e18c7620b45230607c5e81c8dfc1cf59494656e295faeff`.
The receipts bind `--pull=never`, `--network=none`, linux/amd64, and three
unique invocations: two at `/work` and one at `/alternate-work`. All three
produced the same wheel bytes. `wheel-audit.json` records the exact
wheel/RECORD and ELF closure; `cup-native-conformance.json` records clean-env
offline installation and a real depth-8 native measurement of `cup_cup_033`.
That measurement used the explicit `no-provider-configured` path and records
zero provider dispatches; it does not claim an independently measured provider
or network-denial boundary.

`local-development-admission.json` binds the exact SAI-004 candidate commit,
three source-document digests, local Docker archive bytes, and NumPy, Pillow,
and trimesh CAS bytes. `candidate.json` upgrades only
`localDevelopmentAdmission.status=qualified-local-candidate`; it keeps
`admission.admitted=false`, `formalAdmission=false`, and
`immutableMirrorVisible=false`. SAI-003 must not be called Formal, S3-backed,
production-admitted, or complete until those remaining bindings exist.
