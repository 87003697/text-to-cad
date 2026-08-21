#!/usr/bin/env bash
# Launch Playwright MCP and the fixed CAD render service in one job container.
#
# Bind on 0.0.0.0 so docker's port publish can NAT into the container;
# the container itself is confined to a per-job internal bridge, so
# cross-container reach requires the host loopback published port.

set -euo pipefail

HOST="${PW_MCP_HOST:-0.0.0.0}"
PORT="${PW_MCP_PORT:-9223}"
CAD_RENDER_PORT="${TTC_CAD_RENDER_PORT:-9224}"

# @playwright/mcp@0.0.79 resolves `--browser chromium` to a chrome-for-testing
# build (chromium-1237) that this base image does not ship; only
# chromium_headless_shell-1161 is preinstalled. Pin the executable to what
# actually exists so `browser_navigate` can launch.
CHROME_EXECUTABLE="${PW_MCP_EXECUTABLE_PATH:-/ms-playwright/chromium_headless_shell-1161/chrome-linux/headless_shell}"

log() { printf '[browser-runtime] %s\n' "$*" >&2; }

log "starting playwright mcp on ${HOST}:${PORT}"
log "starting fixed CAD render service on ${HOST}:${CAD_RENDER_PORT}"
log "using chromium executable: ${CHROME_EXECUTABLE}"

node /opt/text-to-cad/cad-render-service.cjs &
cad_render_pid=$!

/usr/bin/playwright-mcp \
    --host "$HOST" \
    --port "$PORT" \
    --headless \
    --isolated \
    --browser chromium \
    --executable-path "$CHROME_EXECUTABLE" \
    --no-sandbox \
    --allowed-hosts '*' &
mcp_pid=$!

terminate_children() {
  kill -TERM "$cad_render_pid" "$mcp_pid" 2>/dev/null || true
}

trap terminate_children INT TERM

set +e
wait -n "$cad_render_pid" "$mcp_pid"
status=$?
set -e
terminate_children
wait "$cad_render_pid" 2>/dev/null || true
wait "$mcp_pid" 2>/dev/null || true
exit "$status"
