# syntax=docker/dockerfile:1.7
# Playwright MCP + Chromium runtime container for the bwrap Codex pilot.
#
# The container exposes Streamable HTTP MCP on 9223 and the fixed CAD render
# service on 9224. The pilot supervisor publishes both to random host-loopback
# ports and publishes the resulting job capability read-only into bwrap.
#
# Reproducibility: the base image tag is pinned here; the exact digest
# lands in image-lock.json after the first buildx build (Step 4 of the
# browser-runtime plan). Do not float the base tag without also updating
# image-lock.json.
FROM mcr.microsoft.com/playwright:v1.51.1-jammy

ENV DEBIAN_FRONTEND=noninteractive \
    PW_MCP_VERSION=0.0.79 \
    PW_MCP_HOST=0.0.0.0 \
    PW_MCP_PORT=9223 \
    TTC_CAD_RENDER_PORT=9224 \
    NODE_PATH=/usr/lib/node_modules \
    NODE_ENV=production \
    PLAYWRIGHT_MCP_PING_TIMEOUT_MS=0

RUN npm install --global --omit=dev \
      @playwright/mcp@0.0.79 \
      playwright-core@1.51.1 \
    && test -x /usr/bin/playwright-mcp

COPY packages/browser_runtime/image/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY packages/browser_runtime/image/cad-render-service.cjs \
  /opt/text-to-cad/cad-render-service.cjs
COPY packages/meshshot/src/meshshot/runtime/render.html \
  /opt/text-to-cad/render-programs/residual/render.html
COPY packages/meshshot/src/meshshot/runtime/residual-render.js \
  /opt/text-to-cad/render-programs/residual/residual-render.js
COPY packages/meshshot/src/meshshot/profiles/cadena_residual_eight_view_v1.json \
  /opt/text-to-cad/render-programs/residual/cadena_residual_eight_view_v1.json
RUN chmod 0755 /usr/local/bin/entrypoint.sh

USER pwuser
WORKDIR /home/pwuser
EXPOSE 9223 9224
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
