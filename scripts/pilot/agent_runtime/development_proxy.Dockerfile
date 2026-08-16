FROM python:3.14.4-slim

RUN useradd --system --uid 65532 --no-create-home proxy
WORKDIR /opt/text-to-cad
COPY scripts/pilot/agent_runtime/development_venus_proxy.py scripts/pilot/agent_runtime/development_venus_proxy.py
COPY scripts/pilot/agent-runtime-development-proxy.py scripts/pilot/agent-runtime-development-proxy.py
USER 65532:65532
HEALTHCHECK --interval=5s --timeout=2s --retries=6 CMD ["python3", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=1).read()"]
ENTRYPOINT ["python3", "scripts/pilot/agent-runtime-development-proxy.py"]
