# syntax=docker/dockerfile:1.7
# Playwright MCP + Chromium runtime container for the bwrap Codex pilot.
#
# The container exposes Streamable HTTP MCP on the internal port 9223.
# The pilot supervisor publishes that port to a random host loopback slot
# (docker run -p 127.0.0.1:0:9223), and Codex inside bwrap dials the URL.
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
    NODE_ENV=production

RUN npm install --global --omit=dev @playwright/mcp@0.0.79

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0755 /usr/local/bin/entrypoint.sh

USER pwuser
WORKDIR /home/pwuser
EXPOSE 9223
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
