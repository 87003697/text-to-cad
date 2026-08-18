# Playwright MCP UDS + Auth Spike

Date: 2026-08-18
Ran on: Mac (host), `@playwright/mcp@0.0.79`, node v25.9.0
Context: Step 1 of plan `packages/browser_runtime/` single-container milestone.

## Question

Can Playwright MCP expose its Streamable HTTP endpoint over a UNIX domain
socket (UDS), and does the HTTP server enforce Bearer-token authentication?

If native UDS: the container entrypoint invokes `@playwright/mcp` directly on
the socket. If not: a small in-container shim (socat) fronts a loopback TCP
port with UDS. Either way, no cross-job HTTP surface leaks through the shared
bwrap network namespace.

## Findings

### 1. No native UDS support

`npx @playwright/mcp@latest --help` lists only `--port <port>` ("port to listen
on for SSE transport") and `--host <host>`. **No** `--socket`, `--transport`,
`--uds`, or similar. Streamable HTTP is the only supported transport for
remote clients; stdio is the only alternative.

**Consequence for the container:** we MUST run MCP on loopback TCP inside the
container and shim to UDS. The `entrypoint.sh` in the plan already does this
via socat.

### 2. HTTP server does NOT enforce Bearer auth

Started `@playwright/mcp --port 9223 --host 127.0.0.1 --headless --isolated
--browser chromium` on the host, then:

- `curl -X POST http://127.0.0.1:9223/mcp -H "Host: localhost:9223" -H
  "Authorization: Bearer WRONG" -d '<initialize JSON-RPC>'` → **HTTP 200**,
  full protocol response.
- Same request without `Authorization` header → **HTTP 200**, same response.
- Same request with a `Host:` header that doesn't match binding (e.g.
  `Host: 127.0.0.1:9223` when server bound to `--host 127.0.0.1` but
  advertising `localhost`) → **HTTP 403** "Access is only allowed at
  localhost:9223".

**Interpretation:**
- The MCP HTTP server only guards against **DNS rebinding** via a Host-header
  check (from `--allowed-hosts`, which defaults to the bound host).
- It does **NOT** authenticate the request itself. Any local process that can
  reach the loopback port AND send a matching `Host:` header can drive the
  browser.
- Playwright MCP's own security section already warns: "does not serve as a
  security boundary." The `--allowed-hosts` docs repeat this.

### 3. UDS shim works transparently

Started `python3 uds_shim.py /tmp/mcp.sock 127.0.0.1 9223` (a 30-line
Python UDS→TCP forwarder, functionally equivalent to
`socat UNIX-LISTEN:/tmp/mcp.sock,fork TCP:127.0.0.1:9223`), then:

- `curl --unix-socket /tmp/mcp.sock -X POST http://localhost:9223/mcp -H
  "Host: localhost:9223" -H "Content-Type: application/json" -d
  '<initialize>'` → **HTTP 200**, full MCP `initialize` response with
  `protocolVersion` and `serverInfo`.
- Subsequent `tools/list` returned HTTP 400 "Server not initialized" — this
  is expected Streamable-HTTP MCP semantics (the client must reuse the
  session by sending back the `mcp-session-id` header from the initialize
  response), not a shim defect.

**Consequence:** the shim adds zero protocol layer; requests round-trip end-
to-end. The container can safely use socat (production) or the same script
pattern (dev).

## Design implications for `packages/browser_runtime/`

- **Keep** the `entrypoint.sh` pattern in the plan (MCP on loopback + socat
  UDS listener at `/run/mcp/mcp.sock`). Drop the "if native UDS, replace this
  block" comment — native UDS is confirmed absent.
- **Ownership boundary is fs, not HTTP.** UDS is created with `mode=0660`
  owned by the container's runtime user; host-side, only the per-job
  `browser_capability_dir` (owned by the pilot user, 0750) can reach it.
  bwrap mounts that dir read-only into `/run/meshshot-browser`.
- **No bearer proxy needed.** Adding one would add a same-container shim that
  gives no real isolation beyond what fs permissions already give — and it
  would resurrect the second-image complexity the plan explicitly avoids.
- **Loopback port choice inside container**: 9223 is fine as an internal
  detail; it never leaves the container's network namespace (the container
  can even run with `--network none` after socat is started, since the shim
  only needs `127.0.0.1`). Consider `--network none` in Step 3 to remove
  even the theoretical possibility of cross-container TCP.

## Uncertainty / must-test-later

- Whether Playwright MCP on `--network none` still succeeds — MCP itself
  makes no outbound calls (just drives Chromium), so it should, but this
  needs confirmation in Step 4 (image build) / Step 7 (local smoke).
- Whether Codex CLI Streamable-HTTP MCP client can dial a UDS via the
  `command`/`args` config (using socat as the client-side dialer, mirroring
  the config in `_render_mcp_config`). Verified separately in Step 6 tests
  and Step 7 smoke.
- Playwright MCP session state on top of UDS transport — the initialize +
  subsequent-call flow depends on the client re-sending `mcp-session-id`.
  Codex CLI's MCP client already handles this; only becomes an issue if we
  add our own client (we don't).

## Cleanup

All spike processes killed; port 9223 free; `/tmp/pw-mcp-spike/` retained
locally for repro (`uds_shim.py`, `mcp.log`, `shim.log`). Not committed.
