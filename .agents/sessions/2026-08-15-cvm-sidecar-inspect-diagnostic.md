# CVM Browser Sidecar image-inspect diagnostic handoff

## Objective

Replace the terminal `sidecar-inspect-access` blind spot with one bounded,
ordered diagnostic sequence at the existing public `remote-provision` seam.
The workflow must inspect ID, OS, architecture, then revision, stopping at the
first failed field without publishing Docker output, stderr, paths, errno,
socket data, or daemon JSON.

The originating terminal boundary is recorded in docs-only commit
`24b3fdc7901f09a9c3a4dd567df7baf81c34961b`. The latest handle
`cvmsp-b1ff1da80d7ed0006ff13fff` and every earlier handle are terminal with
`retryAllowed:false`; none may be retried, adopted, inspected, cleaned, or
deleted by this successor.

## Fixed implementation range

- Base reviewed/deployed workflow:
  `c4f1e8385a40043a4147e2b108d7a6f693d63051`
- RED: `4df82a55` — the public receipt still collapsed ID-only command failure
  to `sidecar-inspect-access`.
- GREEN: `ad370aef8d459c4995790d8de802d8207ef57111` — four ordered fixed
  projections with role/field access, timeout, format, and identity checks.
- Review correction: `d70858ffd1621ec18176d1f18046ba275e060324` — shared remote-provision
  fixture, real nonzero command traversal for both roles, and exact transfer
  absence assertions.

## Behavior

For Sidecar first, then sealed client, the workflow runs only these fixed
projections against the exact loaded image ID:

1. `{{.Id}}`
2. `{{.Os}}`
3. `{{.Architecture}}`
4. `{{index .Config.Labels "org.opencontainers.image.revision"}}`

A later command runs only after the prior value is accessible, one non-empty
field, and valid. Each role publishes one of:

- `inspect-<field>-access`, `inspect-<field>-timeout`, or
  `inspect-<field>-format`;
- identity `id`, `os`, `architecture`, or `revision`;
- strict prepare `receipt` mismatch.

All checks remain role-prefixed. The public `prepare`, `provision`, and `probe`
interfaces, archive/image ownership, nonce-scoped abort, no-retry behavior,
and transfer cleanup contract are unchanged.

## Validation

- Tight original RED: 3/3 deterministic failures, 2–4 ms each.
- Sequential role/field matrix and ID-only nonzero cases: PASS.
- Focused CVM Sidecar suite: 38/38 PASS.
- Global policy gate with local loopback permitted: 189/189 PASS.
- Python compilation and scoped diff check: PASS.
- Initial independent review of `c4f1e838..ad370aef`: production behavior had
  no wrong implementation or scope creep; reviewers requested only stronger
  public-command/cleanup coverage and fixture deduplication. Those corrections
  are `d70858ff`; successor dual review is still required.
- Docker/CVM/external operations from this worktree: zero.

## Next authorized boundary

1. Require independent Standards and Spec PASS on one clean successor SHA.
2. Deploy that SHA once through `$cvm-push`.
3. Prepare the unchanged reviewed image pair from runtime source
   `1abe4c97929906b5c0b28b0f3f38857bd923952f`:
   Sidecar `sha256:22ff2413ffd9dcdb5f62e5dbb2c6e46d6b4e98f0e45dc4698f80eb8f06b146f1`
   and sealed client
   `sha256:a2dae48401a6918a15e68a97c4c0290ba6a58ec47a3448498aec12885be46373`.
4. Use exactly one fresh handle for `provision`. Do not inspect raw remote logs
   or retry on failure.
5. If and only if provision succeeds, dispatch the sealed `probe` exactly once
   on that same handle and report its terminal receipt and absence proof.

