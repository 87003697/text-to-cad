---
name: cvm-submit
description: >-
  Submit one detached pilot on CVM and return a stable job handle. Trigger:
  "cvm-submit", "提交 CVM job", "启动 CVM pilot".
---

# CVM submit

Run exactly one local wrapper command:

```bash
scripts/pilot/cvm-submit.sh pilot <object> <group>
```

For the narrow provider-free installed-plugin discovery check, run:

```bash
scripts/pilot/cvm-submit.sh provider-free installed-plugin <group>
```

That mode performs no model inference and accepts no token, model, arbitrary
command, or scenario argument. It binds the current plugin-authority receipt
at submit time, revalidates it in the detached supervisor, and runs only
`codex plugin list --marketplace text-to-cad --json` in a network-unshared
sandbox against job-private authority snapshots.

`group` must use the repository pilot layout:
`YYYYMMDD-HHMMSS-<lowercase-kebab-slug>`, normally the same group passed to
`snapshot-batch.sh`.

The wrapper prints one compact JSON object. Preserve its `job` handle exactly
and hand it to `$cvm-monitor`. Submit only creates the detached job; it does not
wait, pull, upload, clean, cancel, or retry.

Do not replace this workflow with raw SSH, `nohup`, `ps`, `stat`, or `find`.
Submitting again creates a different experiment; never resubmit merely because
a monitor connection was interrupted or a heartbeat is stale.
