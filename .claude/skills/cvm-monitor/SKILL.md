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

## Blocking wait

Normal automation launches exactly one `--wait` command and retains its terminal
session. It holds one keepalive SSH connection while the CVM-side CLI waits for
terminal state.

For Codex, resume a yielded terminal with empty `write_stdin` and set both the
terminal wait and outer orchestration yield to the longest interval the runtime
supports. Treat a quiet tool yield as paused orchestration: immediately re-enter
the same maximum blocking wait. Surface control only when the command returns,
the user asks, a precise user deadline arrives, or the terminal actually times
out or disconnects. Do not turn quiet intervals into progress updates, short
waits, `--once` calls, or remote status probes.

If an interrupted turn loses the terminal session, run one new `--wait` command
for the same stable handle. Never submit a replacement job to recover monitoring.
Do not add periodic `ps`, `stat`, `find`, Git, tap, or job-state polling around
the blocking command.

`health: stale` is diagnostic and is not a failed job or permission to retry.
Tap observation is advisory; `artifact_manifest.json.final_status` remains the
terminal authority. Monitoring does not pull, upload, clean, cancel, or kill.

Exit codes: `0` means `--once` returned or wait saw success; `1` means terminal
failure; `2` means invalid/missing job; `3` is an explicitly requested stale
return; `4` is wait timeout. SSH failure uses SSH's own nonzero status and does
not change or resubmit the detached job.
