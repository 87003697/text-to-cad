---
name: cvm-submit
description: >-
  Submit one detached pilot on CVM and return a stable job handle. Trigger:
  "cvm-submit", "提交 CVM job", "启动 CVM pilot".
---

# CVM submit

Run exactly one local wrapper command:

```bash
scripts/pilot/cvm-submit.sh pilot <object> <group> [--model sol|terra|luna|gpt-5.5] [--plugin-mode direct|e2e] [--view-image|--no-view-image] [--reconstruction-spec|--no-reconstruction-spec]
```

`direct` is the default and preserves the benchmark path: it names
`$mesh-to-cad` in the prompt and disables Codex plugin discovery. Use `e2e`
for a natural-language pilot that exercises the installed plugin when the
runner uses the verified job-private plugin authority and `CODEX_HOME`.
Agent Surface pilots are intentionally candidate-only: they use a minimal
`CODEX_HOME` and the verified Agent Source Projection, so their `e2e` marker
records the requested mode but does not prove installed-plugin skill
discovery. Installed-plugin claims require the installed-plugin smoke or an
e2e run with its authority receipt and rollout; normal benchmark batches
remain `direct`.

The model defaults to the public `gpt-5.5` Responses API slug. Selection
precedence is explicit `--model`, then the `MODEL` environment variable, then
that default. Pass `sol`, `terra`, or `luna` through either selector path to
retain the explicit GPT-5.6 Venus variants; the job state reports the resolved
concrete model.

Both modes write `run/plugin-mode.txt` and expose `plugin_mode` through job
status. Those fields prove the requested mode, not which skill Codex actually
invoked. Confirm the rollout together with the bound plugin-authority receipt
before claiming successful installed-plugin invocation.

`view_image` is enabled by default as the treatment. The pilot prompt requires
visual inspection during setup/initial modeling, Repair Hypothesis parent-child
comparison, and final selection, and the Codex CLI leaves the tool enabled. Use
`--no-view-image` for the control; its prompt explicitly forbids calling
`view_image`. Treatment and control use the same Codex CLI/tool surface; audit
compliance from rollout `view_image` tool-call counts. The explicit
`--view-image` flag is accepted as a treatment reaffirmation. The effective
boolean is persisted as `view_image` in provider-backed job state and exposed
by status. Historical records without the field are treated as controls and
are supervised with an explicit `--no-view-image` flag. The provider-free
installed-plugin check remains model-free and image-free.

Reconstruction Spec is enabled by default for Toys4K pilots. The pilot prompt
asks the model to create and maintain the mutable
`<EXP_DIR>/run/reconstruction-spec.json`; use `--no-reconstruction-spec` for a
controlled opt-out. The legacy `--reconstruction-spec` flag remains accepted
as an explicit reaffirmation. The document is not a Workspace CLI mode or
Workspace experiment field, is not Workspace authority, and does not change
the Workspace schema.

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
