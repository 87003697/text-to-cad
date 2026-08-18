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

# @playwright/mcp@0.0.79 resolves `--browser chromium` to a chrome-for-testing
# build (chromium-1237) that this base image does not ship; only
# chromium_headless_shell-1161 is preinstalled. Pin the executable to what
# actually exists so `browser_navigate` can launch.
CHROME_EXECUTABLE="${PW_MCP_EXECUTABLE_PATH:-/ms-playwright/chromium_headless_shell-1161/chrome-linux/headless_shell}"

log() { printf '[browser-runtime] %s\n' "$*" >&2; }

log "starting playwright mcp on ${HOST}:${PORT}"
log "using chromium executable: ${CHROME_EXECUTABLE}"
exec npx --yes @playwright/mcp@"${PW_MCP_VERSION:-0.0.79}" \
    --host "$HOST" \
    --port "$PORT" \
    --headless \
    --isolated \
    --browser chromium \
    --executable-path "$CHROME_EXECUTABLE" \
    --no-sandbox \
    --allowed-hosts '*'
