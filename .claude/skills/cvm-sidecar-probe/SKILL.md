---
name: cvm-sidecar-probe
description: >-
  Prepare, provision, and probe one exact Browser Sidecar OCI pair on the
  Tencent DevCloud CVM. Trigger: "CVM Browser Sidecar", "provision sidecar",
  "probe sidecar on CVM", "CVM sidecar capability probe".
---

# CVM Browser Sidecar provision and one-shot probe

## Purpose

This is the only repository-approved path for transferring fixed Browser
Sidecar OCI images to CVM and running the narrow provider-free capability
probe. It is not a general image transfer, Docker shell, or pilot runner.

The workflow has three separate public operations:

1. `prepare` is local-only. It verifies exactly two supplied Docker image IDs,
   including each canonical ID, OS, architecture, and immutable
   `org.opencontainers.image.revision` label through four fixed local inspect
   projections, then records their source-image and archive SHA-256
   attestations. The v1 `configSha256` field retains its historical name but is
   exactly the 64-hex digest portion of the canonical local source image ID; it
   is not assumed to remain the loaded image ID across Docker storage backends.
   Prepare creates two fixed handle-and-role-bound archive references, saves
   those references into one opaque archive, and proves the temporary local
   references absent before publishing success. It binds the resulting archive
   bytes and size by SHA-256. There is no public archive/reference input and no
   tar/manifest parser surface. It separately records the clean Git HEAD of
   this provisioning workflow; the two revisions are not required to be the
   same commit.
2. `provision` is an external CVM write. It transfers the fixed archive through
   this wrapper. Before any remote state write it verifies the deployed module
   and wrapper SHA-256 values, binds the exact archive bytes/SHA-256, requires
   free space of at least `3 GiB + archive bytes`, and verifies an accessible
   `linux/amd64` Docker server. It retains the 3 GiB post-transfer gate,
   verifies the archive hash before `docker image load`, then resolves each
   fixed handle-and-role archive reference through
   `docker image ls --all --no-trunc --quiet <reference>`. Every result must be
   exactly one complete canonical `sha256:<64-hex>` loaded image ID, the two
   IDs must differ, and both are recorded as the retained runtime IDs. Each
   reference is derived only from the validated handle and fixed role; it is
   never caller supplied.
   The Docker client output is read incrementally with a 71-byte line ceiling;
   a 4097th entry, an oversized line, invalid ASCII/identity, or the 60-second
   deadline terminates and reaps the client before returning a closed failure.
   The exact archive SHA binds the locally attested source identities to those
   loaded role references; CVM does not depend on its incompatible image-inspect
   path. The workflow removes the transfer archive and intentionally retains
   the two loaded images. Full daemon inspect JSON and raw inventory output are
   neither requested nor published.
3. `probe` is a second external CVM write and the only execution dispatch. It
   runs exactly one sealed probe with `--pull=never`, an internal network,
   fixed resource bounds, read-only filesystems, and exact runtime cleanup.

## Authorization and review gate

- `prepare` requires the final reviewed clean SHA and the exact Sidecar and
  sealed-client image IDs. A tag, manifest-list name, dirty source SHA, parent
  SHA, or floating reference is invalid.
- `provision` and `probe` each require explicit CVM authorization. Provision
  does not imply probe, paid pilot, S3, push, image deletion, or retry.
- Before `provision`, deploy the reviewed wrapper code through `$cvm-push`.
  Never replace either skill with a raw `ssh`, `scp`, `rsync`, Docker command,
  or S3 transfer.
- A failed/interrupted provision or probe is terminal for that handle. The
  wrapper records `retryAllowed:false`; do not resubmit it.
- Remote ownership is established only by the fresh random nonce in the exact
  `remote-begin` receipt. The nonce is generated and durably claimed locally,
  then passed into begin; a lost begin receipt can therefore invoke only an
  exact nonce-scoped abort. It never adopts or deletes a predictable
  pre-existing handle.

## Commands

From the reviewed clean checkout, run local preparation once:

```bash
scripts/pilot/cvm-sidecar-probe.sh prepare \
  --source-revision <40-hex-reviewed-clean-sha> \
  --sidecar-image sha256:<64-hex-image-id> \
  --client-image sha256:<64-hex-image-id>
```

Preserve the returned `handle`. After the separate CVM provision grant:

```bash
scripts/pilot/cvm-sidecar-probe.sh provision <cvmsp-handle>
```

After provision returns `status:"provisioned"` and the separate one-shot probe
grant is present:

```bash
scripts/pilot/cvm-sidecar-probe.sh probe <same-cvmsp-handle>
```

These are the complete public interfaces. There is no argument for a remote
path, command, environment variable, URL, Docker option, Render Program, or
request body.

## Success receipt

Report only the compact JSON receipt fields:

- exact `handle`, `imageSourceRevision`, `workflowSourceRevision`,
  Sidecar/client image IDs and config hashes;
- archive SHA-256 and remote verification result;
- sealed request SHA-256 and fixed probe predicates;
- exact resource ledger, Sidecar terminal state, and labeled absence proof;
- `terminalOperation` and `retryAllowed:false`.

Provision success is accepted only when the remote receipt exactly matches the
prepared handle, archive hash/size, both ordered image roles/IDs/platform/config
hashes/revisions, deployed workflow hashes, transfer absence, and terminal
no-retry operation.

Before the remote probe claims its one shot or creates Docker resources, it
re-verifies the deployed module/wrapper SHA-256 values and the current 3 GiB
free-disk gate. Public probe success is accepted only when SSH exits zero and
the receipt exactly binds the local verified provision, both images and
revisions, workflow hashes, fixed request/result predicates, owned resource
ledger, terminal state, absence proof, retained IDs, and no-retry operation.

The two images in `retainedImageIds` remain provisioned by design. The archive,
client container, Sidecar container, and internal network are owned temporary
resources and must be absent in the receipt. Removing retained images is a
different destructive operation and requires a separate authorization.

## Failure handling

- Image revision/platform/ID/config mismatch: stop before transfer.
- A changed local archive fails its receipt hash/size check before the one-shot
  provision claim or any SSH/rsync transfer. On CVM, exact archive hash, Docker
  load success, and exact handle-and-role reference resolution to two distinct
  loaded IDs are the authoritative runtime-image proof; malformed bytes
  produce the bounded archive/load failure receipt.
- If local prepare fails, cleanup attempts every exact owned temporary archive,
  final archive, temporary role reference, receipt, state directory, and the
  shared root only when empty. Cleanup continues after individual errors. Any
  missing absence proof dominates the original failure as fixed
  `prepare-cleanup-absence`; raw paths, errno, and exception text are not
  published.
- After successful prepare cleanup, an existing fixed `ProbeError` retains its
  check. Any other filesystem/runtime exception is replaced by fixed
  `prepare-operation`; its traceback, path, errno, and message are not
  published.
- Remote begin followed by transfer/finalize failure: the wrapper invokes its
  one fixed abort operation, proves transfer absence when possible, writes a
  terminal failed receipt, and forbids retry.
- Remote provision failures emit and persist a fixed receipt whose
  `errorCheck` is one of `prepare-receipt`, `archive-hash-size`,
  `remote-disk-gate`, `image-load`, `transfer-cleanup`,
  `deployed-workflow-hash`, `image-attestation-unexpected`,
  `image-inventory-access`, `image-inventory-timeout`,
  `image-inventory-format`, or one role-specific `loaded-id` / `receipt` check.
  Inventory overflow, oversized lines, and invalid identities are
  `image-inventory-format`; the fixed read deadline is
  `image-inventory-timeout`.
  Public provision preserves that exact closed check and bounded remote receipt
  before the nonce-scoped abort. Raw stderr, paths, errno, inventory, full
  daemon JSON, and Docker output are not durable evidence.
- Local prepare image-inspect launch/socket/permission exceptions remain
  bounded to the exact role and field's `inspect-<field>-access`; command
  timeouts use `inspect-<field>-timeout`; unexpected single-field projection
  parser exceptions use `inspect-<field>-format`. Neither may publish traceback,
  path, errno, or exception text before local state is committed.
- Probe failure or cleanup failure: preserve the receipt and handle; do not
  rerun, submit a pilot, inspect raw remote state, or invent a cleanup command.
- Missing or malformed receipt: report the missing structured evidence and
  stop. Do not infer success from SSH/rsync/Docker exit alone.

Probe cleanup removes only resources whose exact Docker ID and both ownership
labels (handle plus nonce) are verified. Predictable name collisions are never
cleanup authority. A stop/log/inspect/remove timeout does not skip the remaining
cleanup steps; the durable terminal failure receipt records the first operation
failure, all cleanup errors, and the final absence proof.

## Validation boundary

The focused local suite uses fake Docker/SSH/rsync boundaries and performs no
CVM access:

```bash
python3 -m unittest tests.python.global.test_cvm_sidecar_probe
```

Running `provision` or `probe` against CVM is outside the local test boundary
and always consumes the separately named external authorization.
