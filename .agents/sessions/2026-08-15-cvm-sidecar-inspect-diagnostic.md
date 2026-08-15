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
  are `d70858ff`.
- Successor `70473af2ece6b7c6132bea4232253c30d08b6af3`: independent Standards
  PASS and Spec PASS; full-range diff check PASS; worktree clean.

## Fresh CVM terminal result

- `$cvm-push` deployed clean workflow
  `70473af2ece6b7c6132bea4232253c30d08b6af3` successfully; the remote Git
  base was `no-git` and is not the deployment identity.
- Local prepare created fresh handle `cvmsp-9e254ee6ea0f150e3e5dc030` for the
  unchanged reviewed image pair. The opaque archive is 1,084,572,160 bytes,
  SHA-256 `2ae8ec5106f642aa38cbb20f649c8dce17c885eb6eb22ea98dbd66dffd357e17`.
- The handle's sole provision attempt terminated failed at exact check
  `sidecar-inspect-id-access`. No OS, architecture, revision, or probe command
  ran after the failed ID-only projection.
- Terminal operation is `provision`; `retryAllowed:false`.
- Nonce-scoped abort succeeded with no cleanup errors and
  `transferAbsenceProved:true`. The archive, remote prepare receipt, and
  incoming transfer directory are all proved absent.
- The sealed Chromium probe is `NOT_RUN`; this handle must not be retried,
  adopted, inspected through raw logs, or cleaned by an ad hoc command.

## Completed authorization boundary

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

Steps 1–4 completed. Step 5 was correctly not dispatched because provision
failed terminally at the first Sidecar ID-addressability predicate.

## Portable image-address successor

The terminal CVM result showed that `docker image load` completed but the
daemon rejected the canonical `sha256:<64-hex>` value as the first inspect
address. The identity contract remains canonical; only the fixed Docker CLI
address changes to the bare 64-hex config digest.

- RED `d77bdf1a`: an older-Docker public `remote-provision` fixture rejects the
  canonical address, accepts the bare config digest, and requires the returned
  `.Id` to equal the original canonical identity. Existing code failed in 2 ms
  at `sidecar-inspect-id-access`.
- GREEN `ca66f6af`: every fixed ID/OS/architecture/revision projection uses the
  same bare digest address. There is no runtime fallback, tag, prefix matching,
  or relaxed receipt comparison.
- Focused CVM Sidecar suite: 39/39 PASS.
- Global policy gate with local loopback permitted: 190/190 PASS.
- Python compilation and diff check: PASS.

The next external boundary requires independent Standards and Spec PASS on one
clean successor. Then deploy that exact SHA once, create one new prepare handle,
provision it once, and run the sealed probe only if provision succeeds.

That boundary completed at reviewed/deployed SHA
`05aa03b30aeb4349d6d7f166f2af513a951c3884` with fresh handle
`cvmsp-f12792a693f6a3d162e0f7b0`. Provision again terminated at
`sidecar-inspect-id-access`, proving that the `sha256:` prefix was not the CVM
compatibility defect. The nonce-scoped abort and all three transfer-absence
predicates passed; the sealed probe remained `NOT_RUN`.

## Root inspect command successor

The remaining narrow CLI compatibility seam is the image-inspect command
namespace. The next fixed command is the older, portable root form
`docker inspect --type=image --format`, still addressed by the exact bare
config digest and still requiring returned canonical `.Id` equality.

- RED `9d576c0f`: a legacy-Docker public `remote-provision` fixture rejects
  `docker image inspect` and accepts only the root inspect form. Existing code
  failed in 2 ms at `sidecar-inspect-id-access`.
- GREEN `769835cb`: all four projections use the single root inspect command;
  no command fallback, tag, prefix match, or relaxed identity path exists.
- Focused CVM Sidecar suite: 40/40 PASS.
- Global policy gate with local loopback permitted: 191/191 PASS.
- Python compilation and diff check: PASS.

Require a fresh independent Standards and Spec PASS before one new deployment,
prepare handle, provision attempt, and conditional sealed probe.

That boundary completed at reviewed/deployed SHA
`feafd4301d6c574d71cc788f26a803f488ec6c48` with fresh handle
`cvmsp-469b9884cd56545d8e295048`. Provision again terminated at
`sidecar-inspect-id-access`, proving that neither canonical-vs-bare addressing
nor root-vs-image inspect syntax was the CVM blocker. Nonce abort and all
transfer-absence predicates passed; the sealed probe remained `NOT_RUN`.

## Loaded inventory successor

Remote inspection is unnecessary once the following chain is exact: local
prepare attests canonical config ID + linux/amd64 + immutable source revision;
the complete opaque archive bytes are SHA-bound; CVM verifies the same bytes,
loads them successfully, and proves both exact config IDs occur in its loaded
image inventory. The config digest cryptographically binds the locally
attested fields.

- RED `394283d1`: actual `remote-provision` receives exact loaded Sidecar/client
  inventory while every image-inspect command is unavailable. Existing code
  still failed in 2 ms at `sidecar-inspect-id-access`.
- GREEN `ab70b224`: remote provision runs one bounded fixed
  `docker image ls --no-trunc --quiet`; every line must be a full canonical ID,
  at most 4096 lines are accepted, and both prepared IDs must be present.
  Missing Sidecar/client IDs and inventory access/timeout/format each remain
  closed predicates. Remote raw inventory is never published.
- Local prepare retains the exact four-field image inspection and canonical
  receipt binding.
- Focused CVM Sidecar suite: 40/40 PASS.
- Global policy gate with local loopback permitted: 191/191 PASS.
- Python compilation and diff check: PASS.

Require independent Standards and Spec PASS before one new deployment, prepare
handle, provision attempt, and conditional sealed probe.

## Bounded inventory stream successor

Independent Spec review passed the loaded-inventory identity chain. Standards
found one implementation boundary: the former generic command runner buffered
all Docker inventory output before enforcing the 4096-entry limit.

- RED `cbd668c4`: public `remote-provision` fixtures require exactly 4096 valid
  entries to pass, while entry 4097 and a 72-byte line fail as
  `image-inventory-format`; both overflowing producers remain live until the
  workflow explicitly terminates and reaps them. The buffered implementation
  failed all three cases.
- GREEN uses one dedicated binary pipe reader with a 71-byte pending-line
  ceiling, a 4096-entry counter, and one 60-second monotonic deadline. It stores
  only validated canonical IDs, discards stderr, terminates/reaps the Docker
  client on every terminal path, and retains the existing public check and
  transfer-cleanup receipts.
- The identity model, archive binding, Docker command, public schemas, and
  probe authorization are unchanged.
- Focused CVM Sidecar suite: 41/41 PASS.
- Global modules excluding `test_cvm_push`: 166/166 PASS. The remaining
  `test_cvm_push` cases excluding
  `test_build_input_copy_does_not_follow_checkout_package_symlinks`: 25/25
  PASS. The one excluded test hung inside its existing physical-copy
  subprocess and was interrupted with an exact traceback; the complete global
  wrapper is therefore NOT_RUN, not PASS.
- Python compilation and diff check: PASS.

Require independent Standards and Spec PASS on the clean successor before one
new deployment, prepare handle, provision attempt, and conditional sealed
probe.

That boundary passed independent Standards and Spec review and deployed clean
SHA `547c36f1fc03f18578f3fcc27a66a4f3eb609c76`. Its deterministic local
prepare handle `cvmsp-73f95823109c40120b8bd7f4` terminated before any CVM
provision at:

- operation: `prepare`
- exit: `2`
- bounded failure: `prepare cleanup could not prove absence`
- retained exact temporary archive: `312583168` bytes
- prepare receipt: absent
- CVM provision: `NOT_RUN`
- sealed Chromium probe: `NOT_RUN`

The failed handle is retained and must not be retried, adopted, or cleaned.
Because the handle is derived from image source revision, workflow source
revision, workflow file hashes, and exact image IDs, the same clean SHA must
continue to reject reuse. This docs-only successor records the terminal fact
and intentionally creates a new workflow source revision while leaving runtime
code, fixed image IDs, and image source revision unchanged. Require independent
Standards and Spec PASS before deploying this successor and creating exactly
one new deterministic prepare handle.

That docs-only successor passed independent Standards and Spec review and was
deployed as clean SHA `b4b72d306f40f20b0120d1640e0cfed091b1fe1c`.
Fresh handle `cvmsp-a840718c3160195b93ba8fea` prepared the exact 1,084,572,160
byte archive successfully, but its single provision terminated at:

- operation: `provision`
- exit: `2`
- exact check: `sidecar-loaded-id`
- nonce-scoped abort: succeeded
- transfer archive/incoming/prepare receipt absence: all proved
- retry allowed: `false`
- sealed Chromium probe: `NOT_RUN`

Local read-only reproduction with the same exact images explains the mismatch:
plain `docker image ls --no-trunc --quiet` returns only the sealed client, while
`docker image ls --all --no-trunc --quiet` returns both exact client and
Sidecar IDs. The Sidecar is the client's untagged parent image and Docker's
default inventory hides it.

## All-images inventory successor

- RED `c4eaab6a`: the existing public remote-provision stream seam requires the
  fixed inventory command to include `--all`; all three bounded stream variants
  failed against the prior command.
- GREEN `2ce0221e` changes only the fixed inventory argv to
  `docker image ls --all --no-trunc --quiet` and migrates the exact fake-Docker
  contract. Identity parsing, 4096-line/71-byte/60-second bounds, archive
  binding, first-failure cleanup, receipts, and probe authorization remain
  unchanged.
- Focused CVM Sidecar suite: 41/41 PASS.
- Global modules excluding `test_cvm_push`: 166/166 PASS. The remaining
  `test_cvm_push` cases excluding the same pre-existing
  `test_build_input_copy_does_not_follow_checkout_package_symlinks` hang:
  25/25 PASS.
- Python compilation and diff check: PASS.

Require fresh independent Standards and Spec PASS on the clean successor before
one new deployment, prepare handle, provision attempt, and conditional sealed
probe. Neither failed handle may be retried, adopted, or cleaned.
