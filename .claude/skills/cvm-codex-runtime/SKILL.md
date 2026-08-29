---
name: cvm-codex-runtime
description: >-
  Inspect, install, or probe the closed CVM Codex CLI runtime. Trigger for a
  CVM Codex CLI 0.147.0, 0.148.0, or 0.149.1 update or runtime version admission.
---

# CVM Codex Runtime

Use only the repository wrapper:

```bash
scripts/pilot/cvm-codex-runtime.sh status
scripts/pilot/cvm-codex-runtime.sh probe
scripts/pilot/cvm-codex-runtime.sh install 0.149.1
```

`status` and `probe` are read-only. `install 0.147.0`, `install 0.148.0`, or `install 0.149.1` is an external host write
and requires explicit user authorization. The CLI accepts no other version,
command, package, or selector path.

The install prepares and validates a private same-filesystem staging directory
under the audited `/usr/local` runtime tree, requiring the requested allowlisted
`codex-cli` version. It then atomically publishes the fixed version directory
and switches `/usr/local/bin/codex`. A failed install or version check leaves
the existing selector unchanged. `probe` confirms the selector resolves under
`/usr`, has the exact version, and can run `codex mcp list` without a provider
or model call.

`0.147.0` and `0.148.0` remain available only for rollback, `status`, and
`probe`; they do not qualify the 0.149.1-only nested provider-free gate. After
a successful `install 0.149.1` and probe, run `$cvm-push` to bind the observed
CLI version into a new plugin authority receipt, then run the provider-free
`agent-surface-mcp-injection` gate. A paid pilot is separately authorized.
