# THROWAWAY Browser Sidecar prototype. The only external base is a pre-resolved linux/amd64 child digest.
ARG PLAYWRIGHT_BASE=mcr.microsoft.com/playwright@sha256:83192064c7510f7ee73dd63dc5f22a5e01a92c81a2e6a9c715d9e3fe55471fd9

FROM ${PLAYWRIGHT_BASE} AS viewer-build
WORKDIR /build
COPY packages/cadjs /build/packages/cadjs
COPY packages/implicitjs /build/packages/implicitjs
COPY packages/cadpy /build/packages/cadpy
COPY viewer /build/viewer
RUN cd /build/viewer && npm ci --ignore-scripts && VIEWER_DEFAULT_FILE=browser_sidecar_inspection.step npm run build

FROM ${PLAYWRIGHT_BASE} AS runtime-deps
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
WORKDIR /opt/browser-sidecar/prototype
COPY packages/meshshot/prototypes/browser_sidecar/package.json packages/meshshot/prototypes/browser_sidecar/package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts

FROM ${PLAYWRIGHT_BASE} AS sidecar
ARG PROTOTYPE_SOURCE_REVISION=uncommitted
LABEL org.opencontainers.image.title="meshshot-browser-sidecar-prototype" \
      org.opencontainers.image.version="0.0.0-prototype" \
      org.opencontainers.image.revision="${PROTOTYPE_SOURCE_REVISION}" \
      io.text-to-cad.source-base="9c5b7ea39030a013023a2f06c83b9b869a394861" \
      io.text-to-cad.review-parent="629eaec232ab2816466dafb5182a1bb4fe66295d" \
      io.text-to-cad.throwaway="true" \
      io.text-to-cad.playwright="1.60.0" \
      io.text-to-cad.chromium-revision="1223"
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    NODE_ENV=production \
    HOME=/home/pwuser
WORKDIR /opt/browser-sidecar
COPY --from=runtime-deps /opt/browser-sidecar/prototype/node_modules ./prototype/node_modules
COPY packages/meshshot/prototypes/browser_sidecar/server.mjs ./prototype/server.mjs
COPY models/prototypes/browser_sidecar_cube.stl ./fixtures/cube.stl
COPY models/prototypes/browser_sidecar_inspection.step ./fixtures/browser_sidecar_inspection.step
COPY models/prototypes/.browser_sidecar_inspection.step.glb ./fixtures/.browser_sidecar_inspection.step.glb
COPY packages/meshshot/src/meshshot/runtime ./residual
COPY packages/meshshot/src/meshshot/profiles/cadena_residual_eight_view_v1.json ./profile.json
COPY --from=viewer-build /build/viewer/dist ./viewer/dist
COPY --from=viewer-build /build/viewer/src/server ./viewer/src/server
COPY --from=viewer-build /build/viewer/src/shared ./viewer/src/shared
COPY --from=viewer-build /build/viewer/package.json ./viewer/package.json
COPY --from=viewer-build /build/viewer/node_modules ./viewer/node_modules
COPY --from=viewer-build /build/viewer/packages ./viewer/packages
COPY --from=viewer-build /build/packages ./packages
RUN chown -R pwuser:pwuser /opt/browser-sidecar
USER pwuser
EXPOSE 3000 3001
ENTRYPOINT ["node", "/opt/browser-sidecar/prototype/server.mjs"]
