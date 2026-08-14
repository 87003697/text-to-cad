# Browser Sidecar prototype (throwaway)

Question: can the outer job runtime own one fixed Playwright/Chromium OCI
sidecar while a browser-less nested client runs only the registered CAD Viewer
and residual Render Programs through `playwright.connect()`?

This is deliberately not production code. It does not change
`meshshot.browser_runtime`, expose arbitrary navigation/script inputs, or offer
a CDP/legacy fallback.

The one-command harness builds digest-pinned `linux/amd64` images in the
dedicated Colima profile, then records P0-P3 evidence under a caller-selected
temporary directory:

```sh
python3 packages/meshshot/prototypes/browser_sidecar/harness.py \
  --docker-host unix:///Users/zhiyuanma/.colima/browser-sidecar-prototype/docker.sock \
  --evidence-dir /tmp/browser-sidecar-prototype-evidence
```

Every test container/network name begins with
`meshshot-sidecar-prototype-`. The harness removes only those exact resources
that it creates. It leaves the images and Colima profile for inspection.

Use `--skip-build` only with all three explicit `--expected-*-id` digests and
all three `--expected-*-revision` values. The command must run from a clean
checkout because the harness and its evidence predicates are part of the
receipt. The tested R8 images were built from the per-image revisions shown
below; the later harness/docs commits are not image source revisions:

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

R8's Docker execution used harness/runtime commit `1abe4c97...`. The post-R8
outer-harness hardening range `960ca3f7...cd17a1af` is unit/contract verified
only and was **not** rerun against Docker or CVM.

The harness writes command-level `evidence.json`; the reviewed concise R8
result is `evidence-summary.json`, with the decision, rejected predecessors,
and limitations in `HANDOFF.md`.

The fixed base is Playwright 1.60.0 noble, resolved before pull to the
`linux/amd64` child digest
`sha256:83192064c7510f7ee73dd63dc5f22a5e01a92c81a2e6a9c715d9e3fe55471fd9`.

`Dockerfile.agent` creates the independent nested-client image. Its visible
filesystem contains the sealed client and profile but no Viewer/residual assets
and no browser executable. It uses `playwright-core` only to connect.
