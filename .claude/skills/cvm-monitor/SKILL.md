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

When a launched provider-free scenario exits nonzero, terminal `status` and
`wait` validate the manifest-bound retained deployment authority and then may
publish `scenario_failure` with schema
`cvm.provider-free-scenario-failure/1`. This projection contains the registered
`scenario_identity` and one closed `stage`:

- `viewer_deployment`
- `shipped_tree`
- `cadpy_runtime`
- `viewer_fallback`
- `candidate_workspace`
- `native_measurement`
- `finalization`

`candidate_workspace` and `native_measurement` may also contain one
manifest-bound closed `operation` that identifies the first failing production
contract. Candidate preparation operations are:

- `fixture_availability`
- `canonical_build`
- `reference_preparation`
- `workspace_init`

Native measurement operations are:

- `attempt_begin`
- `voxblame_measure`
- `native_evidence`
- `voxblame_preview`
- `step_publication`

Historical three-field receipts remain valid. An operation on any other stage,
an operation assigned to the wrong stage, or any value outside these lists,
invalidates the scenario failure receipt.

The failure path does not require successful Workspace, runtime-authority, or
Final Delivery artifacts before reporting the primary stage. It still requires
the failure receipt, retained deployed source, sandbox/no-provider proof, and
their exact terminal-manifest bindings. Invalid, tampered, unbound, or
wrong-scenario receipts are rejected. The projection never includes exception
text, command output, paths, argv, environment data, digests, extra fields, or
multiline details. `process_exit_code`, `runner_final_status`, and monitor exit
code `1` remain the terminal failure authorities.

If a provider-free runner terminates before it publishes
`artifact_manifest.json`, terminal `status` and `wait` results may include
`bootstrap_diagnostic` with schema
`cvm.provider-free-bootstrap-diagnostic/1`. Its `phase` is one of
`before-experiment` or `before-artifact-manifest`; its `classification` is a
closed repository-owned value, accompanied by `process_exit_code`:

- `runner-execution-profile-rejected`
- `runner-environment-allowlist-rejected`
- `runner-stripped-name-receipt-rejected`
- `runner-request-digest-rejected`
- `runner-bwrap-path-rejected`
- `runner-runtime-identity-rejected`
- `runner-output-path-rejected`
- `runner-contract-rejected` for other runner-owned failures
- `python-import-failed`
- `runner-entrypoint-unavailable`
- `runner-exited-before-artifact-manifest`
- `runner-terminated-before-artifact-manifest`
- `runner-completed-without-artifact-manifest`

The supervisor examines at most the final 4 KiB of the retained detached log
only for `before-experiment` classification. Once the experiment directory
exists, log output may be workload-controlled, so `before-artifact-manifest`
uses only process and artifact state and does not derive a marker
classification from the log. The monitor does not publish raw log text, paths,
environment names or values, digests, arbitrary suffixes, or multiline output.
This diagnostic is advisory failure evidence. It does not replace
`artifact_manifest.json.final_status` or the retained no-provider proof as
successful execution authority, and it does not authorize cleanup or retry.

Exit codes: `0` means `--once` returned or wait saw success; `1` means terminal
failure; `2` means invalid/missing job; `3` is an explicitly requested stale
return; `4` is wait timeout. SSH failure uses SSH's own nonzero status and does
not change or resubmit the detached job.
