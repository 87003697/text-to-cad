---
name: cvm-browser-runtime
description: >-
  Install or probe the exact Browser Runtime image on CVM. Trigger for CVM
  Browser Runtime image updates, installation, status, and free preflight.
---

# CVM Browser Runtime

The public workflow is `install → probe`. `status` is read-only.

## Install

Run only when the Browser Runtime image changes or the CVM image is missing:

```bash
scripts/pilot/cvm-browser-runtime.sh install \
  --source-revision <40-hex-image-source-sha> \
  --runtime-image sha256:<64-hex-local-image-id>
```

Install streams one temporary archive to CVM, verifies its digest, loads it,
checks `linux/amd64` and the source-revision label, and writes the host-local
lock at:

```text
~/.local/state/text-to-cad/browser-runtime/image-lock.json
```

The retained Docker image may have a content-ID-derived tag so Docker does not
garbage-collect it. That tag is storage ownership only. Production and probe
always read the exact image ID from the host lock and never select a tag.

Ordinary `cad:cvm-push` does not modify the host lock, so code-only iterations
do not reinstall the image.

## Probe

```bash
scripts/pilot/cvm-browser-runtime.sh probe
```

Probe uses the same host lock and `BrowserRuntimeJob` path as production,
renders the fixed residual triangle, and requires container/network cleanup.

## Status

```bash
scripts/pilot/cvm-browser-runtime.sh status
```

Status reads the current host lock and latest install/probe receipts. It does
not retry, install, start a runtime, or mutate CVM state.

## Invariants

- No tag, registry, provider, repo lock, or local Playwright fallback is a
  runtime authority.
- The host lock contains one exact `sha256:` image ID.
- Install is host-serialized and removes its temporary archive and transport
  tag.
- A paid pilot requires a successful probe after the latest install.

`install` and `probe` are separate CVM writes and require explicit
authorization. Neither authorizes a paid pilot or output upload.

## Validation

```bash
python3 -m unittest tests.python.global.test_cvm_browser_runtime
```
