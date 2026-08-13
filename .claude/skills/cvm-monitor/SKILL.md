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
- `preview_runtime`
- `preview_browser_runtime_staging`
- `preview_browser_outer_exec_probe`
- `preview_browser_nested_exec_probe`
- `preview_dependency`
- `preview_browser_launch`
- `preview_browser_launch_process_limit`
- `preview_browser_launch_file_limit`
- `preview_browser_launch_address_space`
- `preview_browser_launch_shared_memory`
- `preview_browser_launch_executable`
- `preview_browser_launch_executable_missing`
- `preview_browser_launch_executable_permission`
- `preview_browser_launch_executable_spawn_permission`
- `preview_browser_launch_sandbox_permission`
- `preview_browser_launch_filesystem_permission`
- `preview_browser_launch_executable_dependency`
- `preview_browser_adapter_profile`
- `preview_browser_identity`
- `preview_browser_profile`
- `preview_browser_prelaunch`
- `preview_browser_readiness`
- `preview_browser_readiness_timeout`
- `preview_browser_connect`
- `preview_browser_cleanup`
- `preview_browser_signal`
- `preview_browser_runtime_evidence`
- `preview_browser_render`
- `preview_browser_result`
- `preview_public_sandbox_setup`
- `preview_public_spawn`
- `preview_public_timeout`
- `preview_public_unclassified_exit`
- `preview_public_result_shape`
- `preview_public_command_evidence_publication`
- `preview_public_failure_diagnostic_publication`
- `preview_public_success_diagnostic_publication`
- `preview_public_wrapper_evidence_publication`
- `step_publication`

The browser-exec diagnostic operations are backed by the manifest-bound
`run/browser-exec-diagnostic.json` receipt. It contains only closed
`passed`/`failed`/`not-run` outcomes for the outer direct Chromium `--version`
probe, the same exact executable through the nested preview sandbox, and the
subsequent Python-owned prelaunched-CDP runtime. The retired Node fields remain
exactly `not-run`; Playwright only attaches with `connect_over_cdp` after Python
has attested and prelaunched the exact browser. The probes use a five-second outer timeout, a closed
`HOME`/`LANG`/`PATH` environment, no provider or network access, and accept only
the exact bounded result for their caller. Raw stdout, stderr, exception text,
environment values, PID, endpoint, argv, and arbitrary operations are never
projected by the monitor. A diagnostic operation is rejected unless its `/5`
receipt has the corresponding exact outcome tuple and manifest binding.
Historical Node-launch outcomes, operations on another stage, or values outside
these lists are rejected by sandbox profile `/15`. The `/15` sandbox also
binds the Python-owned private browser image to the isolated executable tmpfs
at `/meshshot-exec`; arbitrary temporary roots remain forbidden.

Successful preview evidence also carries the closed
`meshshot.prelaunched-cdp-runtime/1` receipt. Its frozen adapter-profile digest
must match the shipped profile, and its Chromium digest must exactly match the
retained deployment/runtime identity. It never exposes the temporary profile,
process group, readiness port, endpoint, environment, stderr, or launch argv.

When the public operation is `preview_browser_identity`, terminal state also
projects one `browser_identity_diagnostic` with schema
`cvm.provider-free-browser-identity-diagnostic/4`. It contains only the first
failing repository-owned substage:

- `private_snapshot_launch_image_identity`
- `live_running_image_identity`
- `loopback_listener_address_ownership`
- `connected_cdp_browser_version_identity`
- `runtime_evidence_cross_binding`

Only `private_snapshot_launch_image_identity` also carries exactly one phase:

- `source_executable_identity`
- `private_tree_materialization`
- `private_launch_image_identity`
- `playwright_package_revision_identity`
- `private_launch_version_execution`
- `private_launch_version_output_identity`

Every other substage must omit phase.

The `playwright_package_revision_identity` phase carries exactly one check:

- `python_distribution_metadata`
- `playwright_package_manifest`
- `browser_manifest_entry`
- `frozen_playwright_version_match`
- `frozen_browser_revision_match`

The `private_launch_version_execution` phase carries exactly one check:

- `sealed_memfd_creation_policy`
- `private_version_probe_spawn`
- `private_version_probe_timeout`

Every other phase and substage must omit check.

The retained `run/browser-identity-diagnostic.json` must name the same substage
and, when applicable, phase and check as `run/scenario-failure.json`, bind that exact
failure receipt by SHA-256, and
itself be bound by `artifact_manifest.json`. Missing, duplicate, reordered,
unknown, inconsistent, unbound, or recomputed evidence is rejected. The monitor
projection strips the binding digest and still exposes no PID, PGID, port,
endpoint, path, inode, executable digest, argv, environment, output, or
exception text. This substage is diagnostic evidence under the existing
`preview_browser_identity` operation; it is not a new operation surface and
does not weaken any browser identity check.

The eight public-wrapper operations distinguish the complete expected boundary
after direct browser probes: pre-public sandbox setup or enforcement evidence,
subprocess spawn, timeout, unclassified nonzero exit, invalid success-result
shape, public-command evidence publication, diagnostic publication while
handling a failed public command, and diagnostic publication after a successful
public command. Each is backed by the manifest-bound
`run/preview-public-wrapper-diagnostic.json` receipt, which contains only its
schema and the exact closed operation. Successful runs bind `passed`; recognized
renderer classifications keep their existing operations and bind that same
operation. No return code, output, exception, argv, environment, digest, or log
content is included.

If publication of that wrapper receipt itself fails, the nonrecursive root
operation is `preview_public_wrapper_evidence_publication`. It is bound only by
`run/scenario-failure.json` and `artifact_manifest.json`; the wrapper receipt
must be absent physically and from the manifest. This exception never accepts
raw exception text, argv, environment, endpoint, path, or a self-authenticating
replacement wrapper receipt.

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
