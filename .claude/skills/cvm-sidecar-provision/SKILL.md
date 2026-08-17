---
name: cvm-sidecar-provision
description: >-
  Provision the fixed Browser Sidecar and Broker OCI images on Tencent
  DevCloud CVM before a runner smoke or pilot. Trigger: "provision Browser
  Sidecar", "deploy Sidecar images to CVM", "CVM runtime images".
---

# CVM Browser runtime image provision

Use this skill only to transfer the two OCI images used by the current runtime:

- Sidecar owns Chromium and the browser session.
- Broker owns the narrow browser request boundary.

The Agent runs in the runner's bwrap sandbox, so it has no OCI image in this
workflow. The existing pilot runner, not this skill, performs capability and
provider validation.

## Prepare

From one reviewed clean checkout, identify the exact local `linux/amd64` image
IDs and their immutable source-revision labels. Then run:

```bash
scripts/pilot/cvm-sidecar-probe.sh prepare \
  --sidecar-source-revision <40-hex-sidecar-source-sha> \
  --sidecar-image sha256:<64-hex-sidecar-image-id> \
  --broker-source-revision <40-hex-broker-source-sha> \
  --broker-image sha256:<64-hex-broker-image-id>
```

Preparation is local and provider-free. It must return one prepared handle
whose ordered image roles are exactly `sidecar`, `broker`.

## Provision

Provision changes CVM state and requires the user's CVM authorization. First
deploy the reviewed checkout with `$cvm-push`, then run exactly:

```bash
scripts/pilot/cvm-sidecar-probe.sh provision <prepared-handle>
```

Accept success only when the receipt binds the prepared archive and both
ordered image roles, verifies `linux/amd64`, records two distinct retained
image IDs, removes the transfer archive, and reports `status:"provisioned"`.

After provisioning, use the existing pilot runner with a provider-free
workload for the runtime smoke. A paid pilot remains a separate authorized
operation. Bind an authorized detached pilot to this provision receipt by
passing the handle as the final argument:

```bash
scripts/pilot/cvm-submit.sh pilot <object> <group> <prepared-handle>
```

The runner accepts no caller-supplied image ID or Docker reference. It resolves
both only from that handle's verified two-role provision receipt.

## Failure boundary

A failed or interrupted handle is terminal. Preserve its structured receipt
and prepare a fresh reviewed handle after fixing the cause. Use the repository
workflow throughout; raw remote copy or Docker mutation is outside this skill.
