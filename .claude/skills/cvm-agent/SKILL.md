---
name: cvm-agent
description: >-
  Run a controlled Codex engineering task on native Linux CVM through Venus,
  then retrieve its patch and review request. Trigger: "CVM Codex", "让 CVM
  agent 修复", "remote Codex adaptation", "cvm-agent".
---

# CVM agent

Use this workflow only after the task, model spend, and CVM execution are
explicitly authorized.

## Workflow

1. Deploy one clean reviewed source with `$cvm-push`.
2. Submit the fixed task and preserve the returned handle:

   ```bash
   scripts/pilot/cvm-agent.sh submit surface-adaptation
   ```

3. Wait once; an interrupted monitor never authorizes resubmission:

   ```bash
   scripts/pilot/cvm-agent.sh monitor --wait <cvma-handle>
   ```

4. Pass the returned `group/exp` unchanged to `$cvm-pull` using
   `--include-byproducts --retain-cvm-source`.
5. Review `candidate.patch`, `report.json`, `run/last-message.json`, and
   `run/codex-events.jsonl` locally. Apply nothing until the parent reviewer
   accepts the patch and reruns repository tests plus Standards/Spec review.

## Boundary

The remote Codex runs as the unprivileged `nobody` identity inside a fresh,
digest-bound `/tmp` source subset with `workspace-write`. Its root-private
baseline and Venus audit are separate from the worker-owned tree. It cannot
write the shared checkout or use the root-owned Docker socket. The fixed prompt
forbids networked tool commands, Docker mutation, formal pilots, dependency
installation, and nested model calls. The supervisor alone copies review
artifacts into `outputs/`.

One handle is one terminal attempt. Only one may be active, the authorization
ledger permits at most ten handles, and each handle permits at most 48 Venus
upstream attempts within 45 minutes. No automatic resubmission is allowed;
parent review is required before any fresh handle. Retain failures. Missing
structured output, unsafe changed paths, workflow-hash mismatch, timeout, or
unavailable usage evidence closes the handle; never reinterpret it as success.
The public result must name the exact deployed source revision and digest,
workflow hashes, model, task, authorization ceiling, upstream-attempt limit, usage,
changed paths, and output experiment.
