FROM meshshot-sidecar-agent-client-prototype@sha256:a2dae48401a6918a15e68a97c4c0290ba6a58ec47a3448498aec12885be46373

ARG BROKER_SOURCE_REVISION
LABEL org.opencontainers.image.title="meshshot-render-program-broker" \
      org.opencontainers.image.revision="${BROKER_SOURCE_REVISION}" \
      io.text-to-cad.browser-sidecar-broker="1" \
      io.text-to-cad.browser-sidecar-broker-base="sha256:a2dae48401a6918a15e68a97c4c0290ba6a58ec47a3448498aec12885be46373"

USER root
WORKDIR /opt/text-to-cad
COPY scripts/pilot/browser_sidecar.py ./scripts/pilot/browser_sidecar.py
COPY packages/meshshot/src/meshshot/profiles/cadena_residual_eight_view_v1.json \
     ./packages/meshshot/src/meshshot/profiles/cadena_residual_eight_view_v1.json
RUN chmod 0555 ./scripts/pilot/browser_sidecar.py \
    && chmod 0444 ./packages/meshshot/src/meshshot/profiles/cadena_residual_eight_view_v1.json
USER pwuser

ENTRYPOINT ["python3", "/opt/text-to-cad/scripts/pilot/browser_sidecar.py", "broker-container"]
