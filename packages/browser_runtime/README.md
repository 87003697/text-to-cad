# browser_runtime

Per-job Chromium runtime for the bwrap Codex pilot. One container exposes two
job-scoped loopback surfaces:

- Playwright MCP for Agent browser tools;
- closed CAD Render Programs used by `meshshot` and `cadgen` snapshot.

The outer runtime (`scripts/pilot/runner.py`) owns lifecycle and publishes both
container ports to random host-loopback ports. It writes a job-private
`runtime.json`, mounts that capability directory read-only at
`/run/meshshot-browser`, and runs a fixed residual render before any paid Agent
workload starts. Missing or invalid CAD render capability fails closed inside a
pilot; it never triggers an Agent-owned Chromium launch.

This is the sole pilot browser path. The Agent receives one read-only runtime
capability and cannot select a URL, image, executable, or fallback renderer.
CAD snapshot and mesh preview fail closed when that capability is absent; their
skill environments contain no Playwright dependency.
The outer runner requires the exact locked image ID and fails before paid work
when the image or fixed render program is unavailable.

Build the image from the repository root so the Dockerfile-specific allowlist
can include the fixed meshshot assets without sending unrelated workspace
content:

```bash
docker buildx build --load \
  --build-arg SOURCE_REVISION="$(git rev-parse HEAD)" \
  --file packages/browser_runtime/image/Dockerfile \
  --tag text-to-cad-browser-runtime:build \
  .
```
