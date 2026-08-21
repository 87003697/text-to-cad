# browser_runtime

Per-job Chromium runtime for the bwrap Codex pilot. One container exposes two
job-scoped loopback surfaces:

- Playwright MCP for Agent browser tools;
- a closed CAD residual-render operation used by `meshshot`.

The outer runtime (`scripts/pilot/runner.py`) owns lifecycle and publishes both
container ports to random host-loopback ports. It writes a job-private
`runtime.json`, mounts that capability directory read-only at
`/run/meshshot-browser`, and runs a fixed residual render before any paid Agent
workload starts. Missing or invalid CAD render capability fails closed inside a
pilot; it never triggers an Agent-owned Chromium launch.

Development-only: this package does not implement Formal fixed-program
authority, gate proofs, or sealed evidence. It provides a general-purpose
Chromium the Agent can drive via MCP tools plus one bounded repository-owned
render operation, isolated per job by Docker container, network, random
loopback ports, token, and read-only capability. See
`docs/adr/0004-own-provider-free-browser-lifecycle-by-authority.md` for the
long-term Sealed design that a later milestone would layer on top.

Build the image from the repository root so the Dockerfile-specific allowlist
can include the fixed meshshot assets without sending unrelated workspace
content:

```bash
docker buildx build --load \
  --file packages/browser_runtime/image/Dockerfile \
  --tag text-to-cad-browser-runtime:build \
  .
```
