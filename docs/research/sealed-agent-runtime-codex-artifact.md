# Sealed Agent Runtime: Codex Artifact Identity

Date: 2026-08-16
Ticket: SAR-004, “Which exact Codex artifact belongs in the sealed runtime?”

## Decision

Use **Codex CLI 0.147.0 for `x86_64-unknown-linux-musl`**, packaged as a
single native executable in the first Agent Runtime Artifact. Do not install
`@openai/codex@latest`, do not run the rolling standalone installer during an
image build, and do not include Node merely to launch Codex.

The preferred upstream acquisition channel is OpenAI's standalone release
channel, whose documented Linux x86_64 asset name is
`codex-x86_64-unknown-linux-musl.tar.gz`. The official Codex CLI page documents
the standalone installer for macOS/Linux, but it does **not** publish a
version-specific asset URL, checksum, signature, or immutable provenance
statement. Therefore `0.147.0` is the selected version, but its Linux bytes are
not admitted to the sealed runtime until the acquisition gate below has
captured and independently verified them. [Official Codex CLI documentation](https://learn.chatgpt.com/docs/codex/cli)

After admission, the authoritative build input is the verified archive copied
unchanged into the project's existing immutable artifact store and addressed
by its recorded SHA-256. Future image builds consume only that archived object
and fail closed on a hash mismatch. They do not resolve “latest” or refetch a
moving upstream URL.

## Why 0.147.0

The locally installed first-party package is exactly `@openai/codex` 0.147.0,
and its direct native executable reports `codex-cli 0.147.0`. This makes
0.147.0 a concrete, already exercised baseline rather than a moving alias.
The installed package manifest also pins the Linux x64 platform package to
`npm:@openai/codex@0.147.0-linux-x64`; it does not infer the platform payload
from `latest`.

Local primary evidence, inspected on 2026-08-16:

- `/opt/homebrew/lib/node_modules/@openai/codex/package.json`, lines 2–3 and
  22–28: package version 0.147.0 and exact per-platform optional dependencies.
- `/opt/homebrew/lib/node_modules/@openai/codex/bin/codex.js`, lines 16–23 and
  79–96: Linux x64 maps to `x86_64-unknown-linux-musl`, and the launcher finds
  and spawns the native executable from the platform package.
- Direct invocation of the installed native Darwin arm64 executable produced
  `codex-cli 0.147.0`; its SHA-256 was
  `19c4f144c5226a9f17c58e6f0fa854843b0f77a6eb420f40e2745a12f10f5d37`.
  This hash identifies only the inspected Mac binary and is **not** the Linux
  lock value.

The official documentation currently recommends the rolling standalone
installer and also exposes npm/Homebrew installation choices. It does not
claim that the displayed installer command selects immutable bytes, so it is
installation guidance rather than an acceptable image-build lock by itself.
[Official Codex CLI installation section](https://learn.chatgpt.com/docs/codex/cli#get-started-with-codex-cli)

## Does the sealed runtime need Node?

**No, provided the admitted Linux native executable passes the gate below.**

There are two distinct artifacts:

1. The npm convenience launcher starts with `#!/usr/bin/env node`; its package
   metadata declares Node `>=16`. Using that launcher therefore requires Node.
2. The launcher resolves a target-specific package and spawns its native
   `vendor/<target>/bin/codex` executable. The locally installed native Mac
   executable runs `--version` directly without the JavaScript launcher.

These facts support a native-only image design, but they do not prove the
Linux binary's runtime dependency closure. The Linux artifact must still pass
`file`, ELF interpreter/dynamic-section inspection, and direct execution in
the exact Noble-based build environment. If it unexpectedly needs Node or an
undeclared shared library, the artifact is rejected rather than silently
expanding the image.

## Acquisition and admission gate

A release-preparation task must perform this once for 0.147.0, outside the
Docker build:

1. Obtain the version-selected `codex-x86_64-unknown-linux-musl.tar.gz` through
   the OpenAI standalone release channel. Record the request URL, redirects,
   final URL, retrieval timestamp, byte length, and response metadata.
2. Compute SHA-256 over the archive before extraction. If upstream metadata
   exposes an independent checksum, record and compare it; otherwise mark
   `upstream_checksum = unavailable` rather than treating the locally computed
   hash as publisher authentication.
3. Verify the archive contains only the expected native entry, extract it
   without following links, and compute the executable SHA-256.
4. On `linux/amd64` Noble, require direct native execution to report exactly
   `codex-cli 0.147.0`. Record `file`, ELF program headers, dynamic dependencies,
   and the resolved runtime library closure.
5. Run the provider-free Codex smoke with Node absent from `PATH` and absent
   from the image. The smoke must exercise the same non-interactive command
   shape used by the pilot supervisor, not only `--version`.
6. Store the unchanged upstream archive in the immutable project artifact
   store. The runtime lock records the archive SHA-256, executable SHA-256,
   byte length, version output, target triple, and artifact-store object
   identity. A Docker build accepts no network and verifies both hashes before
   installation.

If the OpenAI release channel cannot select 0.147.0 deterministically, use the
exact npm platform payload `@openai/codex@0.147.0-linux-x64` only as a fallback
acquisition route. Preserve its tarball and registry integrity value as
additional evidence, but apply the same SHA-256, ELF, direct-run, and immutable
mirroring gates. The local package metadata establishes that this exact
platform package name exists in the 0.147.0 packaging design; it does not
establish the bytes or publisher checksum of the Linux tarball.

## Provenance boundary

No reviewed official OpenAI documentation establishes a publisher-signed
checksum, signature, transparency record, or immutable URL for Codex CLI
0.147.0. Consequently:

- SHA-256 proves that later builds use the same bytes that were admitted; it
  does not by itself prove who published those bytes.
- The receipt may say `byte-locked and acquired from the documented OpenAI
  release channel` after the gate passes.
- The receipt must not say `publisher-signed`, `cryptographically authenticated
  by OpenAI`, or equivalent unless a later first-party signature/checksum source
  is found and verified.

This is the boundary for the first release. Organization-wide signing and
cosign policy remain outside the Wayfinder destination, while deterministic
byte identity remains mandatory.

## Lock fields handed to implementation

The Codex section of the future runtime lock must contain at least:

```json
{
  "version": "0.147.0",
  "target": "x86_64-unknown-linux-musl",
  "upstream_channel": "OpenAI standalone release",
  "upstream_asset": "codex-x86_64-unknown-linux-musl.tar.gz",
  "archive_sha256": "<filled only after admission>",
  "archive_bytes": "<filled only after admission>",
  "executable_sha256": "<filled only after admission>",
  "version_output": "codex-cli 0.147.0",
  "node_required": false,
  "upstream_checksum": "unavailable unless independently discovered",
  "artifact_store_object": "<immutable object identity>"
}
```

The placeholders are deliberate: this research resolved the version, target,
packaging boundary, and admission contract, but did not download or bless
unverified Linux bytes.

## Sources

- [Official Codex CLI documentation](https://learn.chatgpt.com/docs/codex/cli),
  fetched 2026-08-16: supported installation channels and standalone
  macOS/Linux installer.
- Locally installed OpenAI package
  `/opt/homebrew/lib/node_modules/@openai/codex/package.json`, inspected
  2026-08-16: exact package version, Node launcher requirement, and platform
  package mapping.
- Locally installed OpenAI launcher
  `/opt/homebrew/lib/node_modules/@openai/codex/bin/codex.js`, inspected
  2026-08-16: target selection and native executable dispatch.
- Locally installed package README
  `/opt/homebrew/lib/node_modules/@openai/codex/README.md`, lines 14–64,
  inspected 2026-08-16: OpenAI release channel and Linux native archive name.
