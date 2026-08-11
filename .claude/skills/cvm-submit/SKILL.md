---
name: cvm-submit
description: >-
  Submit one detached pilot on CVM and return a stable job handle. Trigger:
  "cvm-submit", "提交 CVM job", "启动 CVM pilot".
---

# CVM submit

Run exactly one local wrapper command. The established full pilot remains:

```bash
scripts/pilot/cvm-submit.sh pilot <object> <group>
```

The provider-free runtime-authority smoke is the separate closed scenario:

```bash
scripts/pilot/cvm-submit.sh provider-free issue15-runtime-authority <group>
```

`provider-free` accepts only repository-registered scenario names. It cannot
dispatch an arbitrary command, executable, script path, or provider request.
Its immutable job state records the scenario identity and versioned bounded
execution profile; the terminal experiment records stripped credential names
without credential values, zero provider/tap requests, the isolated-loopback
sandbox, native backend identity, Viewer deployment/fallback evidence, and the
complete deployed-runtime tree receipt.

`group` must use the repository pilot layout:
`YYYYMMDD-HHMMSS-<lowercase-kebab-slug>`, normally the same group passed to
`snapshot-batch.sh`.

The wrapper prints one compact JSON object. Preserve its `job` handle exactly
and hand it to `$cvm-monitor`. Submit only creates the detached job; it does not
wait, pull, upload, clean, cancel, or retry.

Before launching, submit atomically creates the new job log with owner-only
`0600` permissions. If that path already exists or private creation fails, the
job becomes terminal `failed` with `supervisor launch failed`; the existing log
is retained unchanged and no supervisor starts. A new job log is neither
appended to nor overwritten.

Do not replace this workflow with raw SSH, `nohup`, `ps`, `stat`, or `find`.
Submitting again creates a different experiment; never resubmit merely because
a monitor connection was interrupted or a heartbeat is stale.
