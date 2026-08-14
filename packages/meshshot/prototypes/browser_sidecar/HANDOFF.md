# Browser Sidecar prototype handoff

## Verdict

**ADOPT the architecture for a production spec.** R8 proved that an
outer-owned, digest-identified OCI Sidecar can serve the complete CAD Viewer
and formal eight-view renderer to a browser-less nested client through
`playwright.connect()`. The outer runtime retains exact lifecycle, isolation,
and cleanup ownership.

This branch is primary-source throwaway evidence, not production code. Do not
merge the prototype implementation into `develop` as the production solution.

## Tested runtime versus documentation

The tested runtime and Sidecar/Agent image source is the clean commit
`1abe4c97929906b5c0b28b0f3f38857bd923952f`. The commit containing this
updated handoff is a later **docs-only successor**. It is not an image source
revision and must not be substituted for `1abe4c97...` in the R8 receipt.

The post-R8 outer-harness hardening is the two-commit range
`960ca3f7bcc7f3000d1310864cf0997022a3cf43^..cd17a1afdf6077e7d361ecb26e86cfacf267a88c`.
It adds fail-closed ownership recovery, best-effort cleanup/absence evidence,
detached-process and signal handling, per-image revision validation, and an
unconditional clean-tree gate for build and exact-image replay. Its 11 focused
Python tests and 4 existing Node contract tests passed. It did **not** change
the images or runtime client, and it was not externally rerun against Docker
or CVM; this is deliberately separate from the R8 30/30 receipt.

The legacy public-baseline image is accurately bound to its last runtime
change, `7e9fbbd15a365d5df691a79b0d2352492888d361`.

## Exact tested artifacts

| Artifact | Digest | OCI source revision |
| --- | --- | --- |
| Official Playwright 1.60.0 noble `linux/amd64` child | `sha256:83192064c7510f7ee73dd63dc5f22a5e01a92c81a2e6a9c715d9e3fe55471fd9` | fixed upstream child |
| Sidecar | `sha256:22ff2413ffd9dcdb5f62e5dbb2c6e46d6b4e98f0e45dc4698f80eb8f06b146f1` | `1abe4c97929906b5c0b28b0f3f38857bd923952f` |
| Sealed browser-less Agent client | `sha256:a2dae48401a6918a15e68a97c4c0290ba6a58ec47a3448498aec12885be46373` | `1abe4c97929906b5c0b28b0f3f38857bd923952f` |
| Same-Colima public baseline | `sha256:7a15df89f7e8f194446ba251cfdb280416e85c46b9a514528d4ab221201ca3af` | `7e9fbbd15a365d5df691a79b0d2352492888d361` |

R8 used only the dedicated Colima profile `browser-sidecar-prototype`:
`linux/amd64`, 2 CPU, 4 GiB memory, 20 GiB disk. The default Colima profile was
not modified.

## P0-P3 R8 result

R8 returned `ADOPT`, `executionError=null`, and **30/30 fail-closed predicates
true**.

| Gate | Result | Key evidence |
| --- | --- | --- |
| P0 provisioning | PASS | Exact image IDs and OCI revisions above; Playwright 1.60.0; Chromium revision 1223 / 148.0.7778.96; `linux/amd64`; non-root `pwuser`. |
| P1 lifecycle/isolation | PASS | Read-only root; no mounts, host ports, source aliases, or Agent-visible browser; internal-only network with external egress denied; capability drop, no-new-privileges, bounded CPU/memory/PIDs/shm/tmp/profile; Sidecar closed on SIGTERM with exit 0. |
| P2 registered Render Programs | PASS | One persistent connection ran probe, CAD Viewer, and residual in fresh contexts/pages from an exact-key structured request. Viewer loaded `browser_sidecar_inspection.step`, reported no artifact error, and the real projection control changed `Solid, Orthographic` to `Solid, Perspective`. Screenshot: 60,743 bytes, SHA-256 `ef40fdbc99f49ecda27df4ec3a6352d5349d217baf922acaafb4e72fc054a2c9`. |
| P2 formal eight-view | PASS | Fixed camera/options and view order `+Z, -Z, +Y, -Y, +X, -X, Iso, -Iso`; raw program PNG 24,070 bytes, SHA-256 `17e9213f5117f37d452f1c5679b68a71c8d1d1b6602810037c1b0abd6ac455f7`. |
| P2 public API parity | PASS | Baseline and remote paths both called unchanged `meshshot.render_residual_preview` and returned `RenderedPreview`. Canonical Python/Pillow final PNG bytes/hash/mode/size, profile, views, variant, and evidence hash were identical. Final PNG: 7,844 bytes, RGB 504x1008, SHA-256 `b498c55c68662989a3a95c4925432d61f979183d30c2cdf593e154c7b0ca9d5b`. |
| P3 concurrency | PASS | Both distinct jobs reached hold with distinct model input/output hashes. Negative cross-checks passed for page, localStorage, cookie, console, and filesystem markers. Cancelling A did not cancel B; both Sidecars terminated exit 0. |
| Terminal cleanup | PASS | The central ledger tracked 9 containers and 4 networks; every exact ID was absent, cleanup failures were empty, and named residue was empty. |

The committed structured result is `evidence-summary.json`. The complete local
receipt is
`/tmp/browser-sidecar-prototype-evidence-r2-r8/evidence.json`, 80,756 bytes,
SHA-256
`dc7b29c4109e3ae64c5f1e610363c729e8b7a791b05563f23b6964129142305e`.
It records 134 terminal operations and deliberately remains outside Git.

The durable Viewer inputs are the LFS-managed pair generated from the same
model:

- `models/prototypes/browser_sidecar_inspection.step`
- `models/prototypes/.browser_sidecar_inspection.step.glb`

## Failure history

- **R5 REJECT:** the STL fixture did not expose the required STEP projection
  inspection control.
- **R6 REJECT:** the STEP page opened and the projection control changed, but
  the Viewer correctly reported that its hidden generated GLB was missing.
- **R7 REJECT:** the real STEP/GLB was present, but Playwright Locator
  actionability timed out while attempting to click the otherwise visible
  projection button. Exact cleanup still passed.
- **R8 ADOPT:** a bounded diagnostic showed the static button was connected,
  visible, unobstructed, and unchanged. The sealed client now verifies and
  focuses that real control, activates the real Radix menu by keyboard, selects
  the real Perspective item, and verifies the ARIA transition. It never uses
  `element.click()`, force click, or direct state mutation.

## Reproduce the exact R8 image set

The normal command builds new images from the current checkout:

```sh
python3 packages/meshshot/prototypes/browser_sidecar/harness.py \
  --docker-host unix:///Users/zhiyuanma/.colima/browser-sidecar-prototype/docker.sock \
  --evidence-dir /tmp/browser-sidecar-prototype-evidence
```

The normal build path now requires a clean tracked and untracked tree, and all
three newly built images bind that clean current HEAD. To exercise the exact R8
images with the post-R8 harness, use `--skip-build` and pass both each exact ID
and its exact source revision:

```sh
python3 packages/meshshot/prototypes/browser_sidecar/harness.py \
  --docker-host unix:///Users/zhiyuanma/.colima/browser-sidecar-prototype/docker.sock \
  --evidence-dir /tmp/browser-sidecar-prototype-evidence-r2-r8-replay \
  --skip-build \
  --expected-sidecar-id sha256:22ff2413ffd9dcdb5f62e5dbb2c6e46d6b4e98f0e45dc4698f80eb8f06b146f1 \
  --expected-agent-id sha256:a2dae48401a6918a15e68a97c4c0290ba6a58ec47a3448498aec12885be46373 \
  --expected-legacy-id sha256:7a15df89f7e8f194446ba251cfdb280416e85c46b9a514528d4ab221201ca3af \
  --expected-sidecar-revision 1abe4c97929906b5c0b28b0f3f38857bd923952f \
  --expected-agent-revision 1abe4c97929906b5c0b28b0f3f38857bd923952f \
  --expected-legacy-revision 7e9fbbd15a365d5df691a79b0d2352492888d361
```

## Production constraints learned

- Keep one stable Playwright connection per job and create/close a fresh
  context and page per registered Render Program. Do not expose generic URL,
  JavaScript, executable, environment, path, or browser-argument inputs.
- Pre-provision immutable images. Runtime pulls and browser downloads remain
  closed.
- Preserve the proved public boundary: unchanged
  `meshshot.render_residual_preview`, including canonical Python/Pillow
  post-processing and `RenderedPreview` projection.
- The trusted baked-page threat model currently uses Chromium `--no-sandbox`
  inside a non-root, no-egress, capability-dropped, read-only container. A
  production spec must accept or replace this explicitly, never silently.
- The prototype Agent derives from the fixed Playwright base and deletes
  browser paths. Production should use a genuinely browser-less base to reduce
  size; the proved runtime boundary is no Agent-visible browser and no
  Agent-owned browser process.

## Not run and next boundary

No CVM capability probe or real CVM run, production integration, provider/model
request, push, merge, tracker mutation, or production runtime edit was
performed. The post-R8 harness hardening has focused unit/contract proof but no
external Docker rerun. Independent dual review must bind the tested runtime SHA,
post-R8 harness SHA, and exact image IDs above before any one-time CVM
operation.
