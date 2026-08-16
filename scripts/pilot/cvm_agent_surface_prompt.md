# CVM Browser Surface adaptation

Work only inside the supplied isolated source copy. Diagnose the current native
Linux failure before proposing a change:

`cannot close mounted Agent browser surface`

Use the actual CVM filesystem to reproduce the scanner over the production
read-only mount set. Identify every failing public sandbox target and fixed
scanner reason. Prefer one provider-free diagnostic that collects the complete
set over repeated first-error experiments.

The parent reviewer rejected an earlier approximate reproduction because it
used `(path, path, True)` for system mounts. Production uses
`(path.resolve(), path, True)` in `_readonly_surface_mounts()`. Reproduce that
mapping exactly. On the current CVM image the reviewed provider-free result is
17 roots, no browser findings, and exactly these three failures:

- `/usr`: `cannot inspect mounted browser surface`
- `/etc/pki`: `cannot inspect mounted browser surface`
- `/sys`: `cannot inspect mounted browser surface`

`/etc/localtime` and `/etc/os-release` are public symlink targets but are not
scanner failures after the production source resolution; do not "fix" them.
Determine the precise inaccessible descendants of the three real failures and
whether the smallest safe correction is a predeclared opaque mask, a narrower
runtime mount surface, or another fail-closed mechanism. Any correction must
prove that the hidden or omitted path is not needed by the nested workload and
cannot hide a browser package, executable, or cache that remains reachable in
the sandbox.

If source changes are justified, make the smallest fail-closed correction and
add focused regression tests. Preserve undeclared-root escape, dangling-link,
cycle, identity-change, uninspectable-entry, writable-browser-artifact, and
required-root rejection. Run the focused tests with system `python3`; this
isolated copy has no repository virtualenv.

Allowed source paths are limited to:

- `scripts/pilot/`
- `tests/python/global/`

Treat Docker, S3, SSH, Git remotes, the shared checkout, formal pilots, and
other model calls as unavailable. Inspect Docker-related source if useful, but
do not invoke, build, load, tag, remove, or mutate Docker state. Do not install
dependencies or access the network from tool commands.

Finish with the required structured response. Put any question or requested
decision for the parent reviewer in `review_request`. A speculative patch is
worse than a precise diagnosis-only result.
