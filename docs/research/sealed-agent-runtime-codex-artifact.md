# Sealed Agent Runtime: Codex Artifact Identity

Date: 2026-08-16
Ticket: SAR-004, “Which exact Codex artifact belongs in the sealed runtime?”

## Decision

Use **Codex CLI 0.147.0 for `x86_64-unknown-linux-musl`**, packaged as a
single native executable in the first Agent Runtime Artifact. Do not install
`@openai/codex@latest`, do not run the rolling standalone installer during an
image build, and do not include Node merely to launch Codex.

The preferred upstream acquisition channel is OpenAI's fixed GitHub release
`rust-v0.147.0`. Its Linux archive is asset `504450426`, exactly 98,970,270
bytes with SHA-256
`0246e2e773834e07f0fb5249ed6ebad12e4591e608f8c7bb97dd6a9690544c36`.
It contains exactly one regular file, `codex-x86_64-unknown-linux-musl`, of
258,278,208 bytes with SHA-256
`cb0a15567e9a60a5820d54b0f6ae86d504dc3805c1eab21a47f70e3eb7b73a40`.
The matching Sigstore bundle is asset `504450400`, exactly 8,585 bytes with
SHA-256
`8ea31ab792fe0cfc7ba55c9dfc1836edf166dabf2d564ed7391eed6c7d422b3d`.

That bundle signs the **extracted executable**, not the `.tar.gz`. Admission
therefore requires both successful executable signature verification and an
independent exact single-member archive binding; neither fact substitutes for
the other. The committed
[`proof candidate`](sealed-agent-runtime-codex-0.147.0-proof.md) records the
read-only replay and its machine-readable identities. It is proof-only, not an
admission receipt, and does not remove the remaining mirror, ELF, or smoke
gates. [Official Codex CLI documentation](https://learn.chatgpt.com/docs/codex/cli)

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
installer and also exposes npm/Homebrew installation choices. The displayed
installer command remains installation guidance rather than an image-build
lock by itself. The fixed GitHub release asset, bundle, exact policy, and
immutable mirror receipt together provide the version-specific admission path.
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

1. Retrieve only the fixed archive asset `504450426` and Sigstore bundle asset
   `504450400` from OpenAI's `rust-v0.147.0` release. Record exact URLs,
   redirects, response metadata, byte lengths, and the annotated tag object
   `3ed6f04f6bf8b7c46299d1cb1ff99c74ce21a51d` peeled to commit
   `be6e8eac029b183056b7e4402879f15d2c85f61b`.
2. Require the archive and bundle byte lengths and SHA-256 values printed in
   the Decision section. Mirror those unchanged bytes before any image build.
3. List the archive without following links or extracting paths. Require
   exactly one regular member named `codex-x86_64-unknown-linux-musl`, no link
   and no traversal, then safely extract it and require the exact executable
   byte length and SHA-256 above.
4. Validate the exact canonical signature policy and verification-receipt
   schemas in the receipt contract. Run the exact mirrored Cosign 2.4.1
   `darwin/arm64` verifier bytes with SHA-256
   `13343856b69f70388c4fe0b986a31dde5958e444b41be22d785d3dc5e1a9cc62`
   against the mirrored TUF/trusted-root closure. No ambient `~/.sigstore`
   state or online trust lookup is authority.
5. Verify the bundle over the extracted executable with the exact SAN, OIDC
   issuer, repository, workflow, tag ref, workflow commit, and trigger below;
   require `Verified OK` and the fixed Rekor inclusion. As a mandatory negative
   control, verify that presenting the archive as payload is rejected because
   its digest differs from the bundle payload digest.
6. On `linux/amd64` Noble, require direct native execution to report exactly
   `codex-cli 0.147.0`. Record `file`, ELF program headers, dynamic dependencies,
   and the resolved runtime library closure.
7. Run the provider-free Codex smoke with Node absent from `PATH` and absent
   from the image. The smoke must exercise the same non-interactive command
   shape used by the pilot supervisor, not only `--version`.
8. Publish immutable acquisition, policy, signature-verification, mirror, ELF,
   and smoke receipts. A network-disabled image build accepts only their exact
   identities and rechecks the archive/member relationship before installation.

The npm platform payload is not a fallback for this first-release admission:
it is not the payload bound by the fixed executable signature policy. Using it
would require a separately reviewed artifact identity and signature policy,
not silent substitution under the `0.147.0` label.

## Provenance boundary

The fixed OpenAI release includes a Sigstore bundle whose payload digest is the
native executable digest. Offline Cosign verification succeeded with exact SAN
`https://github.com/openai/codex/.github/workflows/rust-release.yml@refs/tags/rust-v0.147.0`
and OIDC issuer `https://token.actions.githubusercontent.com`. The certificate
also binds repository `openai/codex`, workflow `rust-release`, ref
`refs/tags/rust-v0.147.0`, commit
`be6e8eac029b183056b7e4402879f15d2c85f61b`, and trigger `push`. Rekor log
`c0d23d6ad406973f9559f3ba2d1ca01f84147d8ffc5b8445c224f98b9591801d`
contains index `2363083279` for that payload.

The version-specific canonical policy digest is
`sha256:283a3458787b25f5d18b86b8967f81147b255c63d15dae2a432d3a6db7e77b29`.
It binds the exact release/tag/workflow/action, certificate identity, bundle,
Cosign verifier, and TUF/trusted-root closure recorded in the proof candidate.
The corresponding proof-only signature-verification receipt digest is
`sha256:beca82ea9864536e5200b837b2f136620dcefab1b1c3cc3e58087ad133d98d00`.
These digests identify canonical documents defined by the receipt contract;
they do not by themselves complete SAI-004 admission.

The correct claim boundary is:

- The extracted executable is byte-locked and its fixed Sigstore bundle
  verifies under the exact policy and trust identities above.
- The archive is **not** signed directly. Its authority comes from its own
  fixed digest plus the independently checked one-regular-member relation to
  the signed executable. The archive negative control must remain part of the
  verification receipt.
- A formal `codex-admission` child may claim signature verification only after
  the verifier, trust material, bundle, policy, receipt, archive/member binding,
  immutable mirror, ELF closure, and both smoke predicates all succeed.
- The committed proof candidate did not publish the immutable project mirror,
  run the Noble/Node-absent/noninteractive smokes, or emit an admission child.
  It must not be described as `Codex admitted`, `Agent Runtime Verified`, or as
  a signature over the archive.

## Lock fields handed to implementation

The Codex section of the future runtime lock must contain at least:

```json
{
  "version": "0.147.0",
  "target": "x86_64-unknown-linux-musl",
  "upstream_channel": "https://github.com/openai/codex/releases/tag/rust-v0.147.0",
  "upstream_asset": "codex-x86_64-unknown-linux-musl.tar.gz",
  "archive_asset_id": 504450426,
  "archive_sha256": "0246e2e773834e07f0fb5249ed6ebad12e4591e608f8c7bb97dd6a9690544c36",
  "archive_bytes": 98970270,
  "archive_member_count": 1,
  "archive_member_name": "codex-x86_64-unknown-linux-musl",
  "archive_member_type": "regular-file",
  "archive_signed_directly": false,
  "executable_sha256": "cb0a15567e9a60a5820d54b0f6ae86d504dc3805c1eab21a47f70e3eb7b73a40",
  "executable_bytes": 258278208,
  "signature_bundle_asset_id": 504450400,
  "signature_bundle_sha256": "8ea31ab792fe0cfc7ba55c9dfc1836edf166dabf2d564ed7391eed6c7d422b3d",
  "signature_bundle_bytes": 8585,
  "signature_policy_digest": "sha256:283a3458787b25f5d18b86b8967f81147b255c63d15dae2a432d3a6db7e77b29",
  "signature_verification_receipt_digest": "sha256:beca82ea9864536e5200b837b2f136620dcefab1b1c3cc3e58087ad133d98d00",
  "verifier_binary_sha256": "13343856b69f70388c4fe0b986a31dde5958e444b41be22d785d3dc5e1a9cc62",
  "trusted_root_sha256": "6494e21ea73fa7ee769f85f57d5a3e6a08725eae1e38c755fc3517c9e6bc0b66",
  "version_output": "codex-cli 0.147.0",
  "node_required": false,
  "retrieval_receipt_digest": "<filled by formal admission>",
  "elf_closure_digest": "<filled by formal admission>",
  "artifact_store_object": "<immutable object identity>"
}
```

Only the retrieval, ELF, and immutable-store fields remain placeholders. The
upstream artifact, archive/member, bundle, signature policy, verifier, and
trusted-root identities are fixed by the committed proof. The placeholders
still prevent this research note from being mistaken for completed admission.

## Sources

- [Committed Codex 0.147.0 signed-byte proof](sealed-agent-runtime-codex-0.147.0-proof.md)
  and its
  [machine-readable companion](sealed-agent-runtime-codex-0.147.0-proof-candidate.json),
  observed 2026-08-16: exact archive/member/bundle, certificate, workflow,
  Rekor, verifier, trusted-root, replay, and negative-control identities.
- [OpenAI Codex `rust-v0.147.0` release](https://github.com/openai/codex/releases/tag/rust-v0.147.0)
  and [official release API record](https://api.github.com/repos/openai/codex/releases/tags/rust-v0.147.0):
  fixed archive and Sigstore bundle assets.
- [Pinned OpenAI release workflow](https://github.com/openai/codex/blob/be6e8eac029b183056b7e4402879f15d2c85f61b/.github/workflows/rust-release.yml)
  and [Linux signing action](https://github.com/openai/codex/blob/be6e8eac029b183056b7e4402879f15d2c85f61b/.github/actions/linux-code-sign/action.yml):
  executable signing occurs before archive creation.
- [Official Cosign 2.4.1 release](https://github.com/sigstore/cosign/releases/tag/v2.4.1)
  and [checksums](https://github.com/sigstore/cosign/releases/download/v2.4.1/cosign_checksums.txt):
  exact verifier binary identity used by the offline replay.
- [Sigstore production TUF repository](https://tuf-repo-cdn.sigstore.dev/):
  exact root, timestamp, snapshot, targets, and trusted-root objects are pinned
  by digest in the proof candidate.
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
