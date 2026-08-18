#!/usr/bin/env bash
# Launch Playwright MCP on the container-internal port. The pilot
# supervisor publishes that port to a random host loopback slot via
# `docker run -p 127.0.0.1:0:9223`, then Codex inside bwrap dials the
# resulting URL through its mcp_servers.browser config.
#
# Bind on 0.0.0.0 so docker's port publish can NAT into the container;
# the container itself is confined to a per-job internal bridge, so
# cross-container reach requires the host loopback published port.

set -euo pipefail

HOST="${PW_MCP_HOST:-0.0.0.0}"
PORT="${PW_MCP_PORT:-9223}"

log() { printf '[browser-runtime] %s\n' "$*" >&2; }

log "starting playwright mcp on ${HOST}:${PORT}"
exec npx --yes @playwright/mcp@"${PW_MCP_VERSION:-0.0.79}" \
    --host "$HOST" \
    --port "$PORT" \
    --headless \
    --isolated \
    --browser chromium \
    --no-sandbox \
    --allowed-hosts '*'
