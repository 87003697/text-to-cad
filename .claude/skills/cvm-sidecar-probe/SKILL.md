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

1. `prepare` is local-only. It saves exactly two supplied Docker image IDs,
   verifies both are `linux/amd64`, binds both immutable
   `org.opencontainers.image.revision` labels to the reviewed clean SHA, and
   records their config and archive SHA-256 attestations. It separately records
   the clean Git HEAD of this provisioning workflow; the two revisions are not
   required to be the same commit.
2. `provision` is an external CVM write. It transfers the fixed archive through
   this wrapper. Before any remote state write it verifies the deployed module
   and wrapper SHA-256 values and enforces the mandatory 3 GiB CVM free-disk
   gate. It verifies the archive hash before `docker image load`, re-inspects
   the loaded IDs/platform/config, removes the transfer archive, and
   intentionally retains the two provisioned images.
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
- Remote begin followed by transfer/finalize failure: the wrapper invokes its
  one fixed abort operation, proves transfer absence when possible, writes a
  terminal failed receipt, and forbids retry.
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
