# CVM Broker readiness correction

Work only inside the supplied isolated source copy. Diagnose the current
provider-free native-Linux failure before proposing a change:

`Browser Sidecar failed (broker-readiness-exit): Broker stopped before readiness`

The deployed runtime has exactly two OCI roles: Sidecar and Broker. The Agent
runs in the existing bwrap sandbox; it is not a third image. Preserve this
architecture and keep the runtime generic. Do not add a Toys4K-specific entry,
fixture policy, supervisor, evidence schema, or image.

The reviewed zero-paid smoke used the current provision receipt and `/bin/true`.
It proved that the Sidecar became ready and closed exactly, while the Broker
process exited before publishing its ready record. No provider request or paid
pilot ran. Treat this as the red public seam. Trace the exact Broker command,
environment, mounted inputs, authority connection, ready-record validation, and
cleanup ordering in repository source. Use static evidence and focused
provider-free tests to distinguish an entrypoint crash from a host-side
readiness mismatch. Do not invent runtime observations that the isolated worker
cannot make.

Produce the smallest fail-closed candidate patch that makes the existing
generic Broker reach readiness on native Linux. Preserve immutable provision
identity, two-role accounting, closed authority, external-egress rejection,
no-retry behavior, terminal evidence, cleanup, and Cup defaults. Prefer a
focused regression test that goes red on the discovered defect and green on the
correction. Run the focused tests with system `python3`; this isolated copy has
no repository virtualenv.

Allowed source paths are limited to:

- `scripts/pilot/`
- `tests/python/global/`

Treat Docker, S3, SSH, Git remotes, the shared checkout, formal pilots, and
other model calls as unavailable. Inspect Docker-related source if useful, but
perform no Docker operation. Install nothing and issue no networked tool
command.

Finish with the required structured response. Put any question or requested
decision for the parent reviewer in `review_request`. A speculative patch is
worse than a precise diagnosis-only result.
