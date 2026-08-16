# Codex 0.147.0 signed-byte proof candidate

Date observed: 2026-08-16

Ticket: SAI-004

Status: proof-only evidence; **not an admission receipt**

This note closes the read-only fact gap discovered while starting SAI-004. It
does not implement an admission schema, publish a mirror, run Codex on Noble,
or claim `Agent Runtime Verified`. The machine-readable companion is
[`sealed-agent-runtime-codex-0.147.0-proof-candidate.json`](sealed-agent-runtime-codex-0.147.0-proof-candidate.json).

## Result

OpenAI's fixed `rust-v0.147.0` release now includes a Sigstore bundle for the
Linux x86-64 native Codex executable. This supersedes the earlier research
assumption that no signature material existed.

The signature covers the **uncompressed executable**, not the `.tar.gz`:

| Object | Exact identity |
| --- | --- |
| Release | GitHub release `366471016`, tag `rust-v0.147.0`, published `2026-08-07T01:41:49Z` |
| Annotated tag | object `3ed6f04f6bf8b7c46299d1cb1ff99c74ce21a51d`, peeled commit `be6e8eac029b183056b7e4402879f15d2c85f61b` |
| Archive asset | ID `504450426`; `codex-x86_64-unknown-linux-musl.tar.gz`; 98,970,270 bytes; SHA-256 `0246e2e773834e07f0fb5249ed6ebad12e4591e608f8c7bb97dd6a9690544c36` |
| Archive member | exactly one regular file named `codex-x86_64-unknown-linux-musl`; 258,278,208 bytes; SHA-256 `cb0a15567e9a60a5820d54b0f6ae86d504dc3805c1eab21a47f70e3eb7b73a40` |
| Bundle asset | ID `504450400`; 8,585 bytes; SHA-256 `8ea31ab792fe0cfc7ba55c9dfc1836edf166dabf2d564ed7391eed6c7d422b3d` |

GitHub's release API supplied the archive and bundle asset IDs, names, byte
counts, URLs and SHA-256 digests. The downloaded bytes matched those values.
The archive was listed before extraction and contained no second member, link,
or path traversal. Production admission must enforce those properties rather
than trusting this observation.

## Exact signing identity

Cosign verified the executable bundle with all identity fields closed to
literals:

- Fulcio SAN URI:
  `https://github.com/openai/codex/.github/workflows/rust-release.yml@refs/tags/rust-v0.147.0`
- OIDC issuer: `https://token.actions.githubusercontent.com`
- repository: `openai/codex`
- workflow: `rust-release`
- ref: `refs/tags/rust-v0.147.0`
- workflow commit: `be6e8eac029b183056b7e4402879f15d2c85f61b`
- trigger: `push`

The signing certificate SHA-256 fingerprint is
`0cd70c48dbbb777f1910538d62604b16be271028b8195325bb8eae58fcf255c8`.
It was valid from `2026-08-07T01:02:23Z` through
`2026-08-07T01:12:23Z`. Rekor log
`c0d23d6ad406973f9559f3ba2d1ca01f84147d8ffc5b8445c224f98b9591801d`
recorded index `2363083279` at `2026-08-07T01:02:25Z`, inside that interval,
and its hashedrekord payload digest is the executable digest above.

The fixed upstream workflow at the signed commit has SHA-256
`62367daacaabcc8972b6f0a60d2f964bd957e7ec68cab5d62756fd494041d183`.
Its `.github/actions/linux-code-sign/action.yml` has SHA-256
`4e5fa040cf838f087ce4a0c585f651e90111b4a02973458b926d6938a24108e5`.
That action calls `cosign sign-blob` on the executable before the workflow
creates the `.tar.gz`; therefore archive hashing plus closed single-member
extraction is a separate required binding.

## Verifier and trust material

The upstream signing workflow pins `sigstore/cosign-installer` commit
`dc72c7d5c4d10cd6bcb8cf6e3fd625a9e5e537da`, whose default verifier is
Cosign 2.4.1. The observed verifier was the official raw
`cosign-darwin-arm64` release asset (ID `196693093`, 108,805,570 bytes), not an
archive. Its SHA-256 is
`13343856b69f70388c4fe0b986a31dde5958e444b41be22d785d3dc5e1a9cc62`.
That value appears both in the pinned installer action and the official
`cosign_checksums.txt`; the checksum file itself is 3,906 bytes with SHA-256
`5020625e52f7041b9e4a21ee7ef4e2d085d767e72f86e2458443b012b0200362`.
The Cosign tag object is `531befdf6581582e22eda7cda084565bb106efa6`
and peels to commit `9a4cfe1aae777984c07ce373d97a65428bbff734`.

The successful replay used Cosign's production TUF client once to acquire
trust material, then verified offline. The exact observed chain was:

| TUF object | Version | Bytes | SHA-256 | Expiry |
| --- | ---: | ---: | --- | --- |
| `15.root.json` | 15 | 5,630 | `73747011d0857ada15479a16c4cae0f3ed03aac698b523b97e1de314ac9d9ca8` | `2026-11-20T13:58:18Z` |
| `timestamp.json` | 757 | 449 | `367992e4f09fbdb98f05cbf4433a3e6d3830d34c230eebd955fb20ccb5c0a956` | `2026-08-23T01:53:11Z` |
| `165.snapshot.json` | 165 | 1,760 | `8f784ab614ec62bfdd5f568eb2a2e3011668449ba235ed4eb7befa99f8469933` | `2036-05-15T08:09:16Z` |
| `14.targets.json` | 14 | 4,942 | `6a697f7f8908c8ab26c11786ecb490b54acec97fa8c802e399f065f8a0cc1acd` | `2036-05-09T09:00:52Z` |
| `trusted_root.json` target | n/a | 6,787 | `6494e21ea73fa7ee769f85f57d5a3e6a08725eae1e38c755fc3517c9e6bc0b66` | bound by targets v14 |

The target URL is the consistent-snapshot object
`https://tuf-repo-cdn.sigstore.dev/targets/6494e21ea73fa7ee769f85f57d5a3e6a08725eae1e38c755fc3517c9e6bc0b66.trusted_root.json`.
The formal admission should mirror these exact bytes and bind the verifier,
trusted-root target and TUF acquisition receipt. It must not read an ambient
`~/.sigstore` cache. The short-lived timestamp makes this observation
time-bounded; this note is not a permanent substitute for an authenticated
admission operation.

## Exact replay and negative control

After safely extracting the only regular archive member, the successful
offline command was:

```text
cosign verify-blob --offline \
  --bundle codex-x86_64-unknown-linux-musl.sigstore \
  --certificate-identity https://github.com/openai/codex/.github/workflows/rust-release.yml@refs/tags/rust-v0.147.0 \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-github-workflow-name rust-release \
  --certificate-github-workflow-ref refs/tags/rust-v0.147.0 \
  --certificate-github-workflow-repository openai/codex \
  --certificate-github-workflow-sha be6e8eac029b183056b7e4402879f15d2c85f61b \
  --certificate-github-workflow-trigger push \
  codex-x86_64-unknown-linux-musl
```

It returned exactly `Verified OK`. Replacing the final executable path with
the `.tar.gz` returned exit 1 and identified the mismatch:

```text
bundle=cb0a15567e9a60a5820d54b0f6ae86d504dc3805c1eab21a47f70e3eb7b73a40
payload=0246e2e773834e07f0fb5249ed6ebad12e4591e608f8c7bb97dd6a9690544c36
```

This negative control prevents the later schema from silently treating an
executable signature as an archive signature.

## Required contract correction

At this proof's observation time, the then-current closed `codex-admission`
predicate `publisherSignatureClaimAbsent` could not represent these facts, so
SAI-004 implementation was stopped pending contract correction. The later
reviewed contract now has distinct archive, executable, bundle, signing-policy,
verifier, normative byte-approval, non-authoritative retrieval-metadata, and
verification-result bindings; its current normative definition is
[`agent-runtime-verification-receipt.md`](../design/agent-runtime-verification-receipt.md#codex-01470-signature-policy-and-receipt).
This historical proof and its machine-readable candidate remain immutable
observations and are not themselves an admission receipt.
