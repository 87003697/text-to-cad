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

Use `--skip-build` to verify already-built exact local images without pulling
or rebuilding them. The harness writes command-level `evidence.json`; the
reviewed concise result is `evidence-summary.json`, with the decision and
limitations in `HANDOFF.md`.

The fixed base is Playwright 1.60.0 noble, resolved before pull to the
`linux/amd64` child digest
`sha256:83192064c7510f7ee73dd63dc5f22a5e01a92c81a2e6a9c715d9e3fe55471fd9`.

`Dockerfile.agent` creates the independent nested-client image. Its visible
filesystem contains the sealed client and profile but no Viewer/residual assets
and no browser executable. It uses `playwright-core` only to connect.
