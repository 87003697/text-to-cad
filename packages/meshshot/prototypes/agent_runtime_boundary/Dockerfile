# THROWAWAY SAR-003 seam prototype. The base must already be admitted by digest.
ARG AGENT_BASE_IMAGE
FROM ${AGENT_BASE_IMAGE}

ARG PROTOTYPE_SOURCE_REVISION=uncommitted
ARG RUNTIME_MANIFEST_DIGEST
LABEL org.opencontainers.image.title="text-to-cad-agent-runtime-boundary-prototype" \
      org.opencontainers.image.revision="${PROTOTYPE_SOURCE_REVISION}" \
      io.text-to-cad.throwaway="true" \
      io.text-to-cad.agent-boundary-schema="meshshot.agent-boundary/1" \
      io.text-to-cad.runtime-manifest-digest="${RUNTIME_MANIFEST_DIGEST}" \
      io.text-to-cad.browser-deny="required" \
      io.text-to-cad.docker-authority="absent"

COPY --chmod=0555 packages/meshshot/prototypes/agent_runtime_boundary/entrypoint.py \
  /opt/text-to-cad/agent-boundary-entrypoint

USER 1000:1000
ENTRYPOINT ["/opt/text-to-cad/agent-boundary-entrypoint"]
