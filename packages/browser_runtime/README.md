# browser_runtime

Per-job Chromium + Playwright MCP container for the bwrap Codex pilot.

The outer runtime (`scripts/pilot/runner.py`) owns lifecycle. Nested Agent
connects to the MCP server via a UNIX domain socket bind-mounted at
`/run/meshshot-browser/mcp.sock` inside the sandbox. There is no HTTP port
exposed to the sandbox and no cross-job Docker network.

Development-only: this package does not implement Formal fixed-program
authority, gate proofs, or sealed evidence. It provides a general-purpose
Chromium the Agent can drive via MCP tools, isolated per job by Docker
container + Docker network + UDS + fs permissions. See
`docs/adr/0004-own-provider-free-browser-lifecycle-by-authority.md` for the
long-term Sealed design that a later milestone would layer on top.
