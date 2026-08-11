---
name: cvm-monitor
description: >-
  Read or wait for a detached CVM pilot by stable handle. Trigger:
  "cvm-monitor", "监控 CVM job", "等待 CVM pilot".
---

# CVM monitor

Use the handle returned by `$cvm-submit`:

```bash
scripts/pilot/cvm-monitor.sh --once <handle>
scripts/pilot/cvm-monitor.sh --wait <handle>
scripts/pilot/cvm-monitor.sh --wait --until terminal-or-stale <handle>
scripts/pilot/cvm-monitor.sh --wait --timeout <seconds> <handle>
```

Normal automation uses one `--wait` call. It holds one keepalive SSH connection
while the CVM-side CLI reads the small job state and returns only at terminal,
an explicitly requested stale condition, timeout, or SSH failure. Do not add
periodic `ps`, `stat`, `find`, Git, or tap polling around it.

`health: stale` is diagnostic and is not a failed job or permission to retry.
Tap observation is advisory; `artifact_manifest.json.final_status` remains the
terminal authority. Monitoring does not pull, upload, clean, cancel, or kill.
Provider-free jobs intentionally have no mandatory tap. Their public state
includes `kind: provider-free` and the registered scenario identity; the
terminal no-provider proof and runtime-authority receipt remain artifact
evidence rather than monitor inference.

If a provider-free runner terminates before it publishes
`artifact_manifest.json`, terminal `status` and `wait` results may include
`bootstrap_diagnostic` with schema
`cvm.provider-free-bootstrap-diagnostic/1`. Its `phase` is one of
`before-experiment` or `before-artifact-manifest`; its `classification` is a
closed repository-owned value, accompanied by `process_exit_code`. The
supervisor examines at most the final 4 KiB of the retained detached log for
classification and does not publish raw log text, environment values, or
multiline output. This diagnostic is advisory failure evidence. It does not
replace `artifact_manifest.json.final_status` or the retained no-provider proof
as successful execution authority, and it does not authorize cleanup or retry.

Exit codes: `0` means `--once` returned or wait saw success; `1` means terminal
failure; `2` means invalid/missing job; `3` is an explicitly requested stale
return; `4` is wait timeout. SSH failure uses SSH's own nonzero status and does
not change or resubmit the detached job.
