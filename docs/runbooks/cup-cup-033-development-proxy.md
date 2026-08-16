# cup_cup_033 Development Venus proxy

This is a Development/MVP path. It is **Not Sealed**, **Not Formal**, and not a
production completion authority. The proxy makes no automatic job or transport
retry. Every accepted `POST /v1/responses` is one independently reserved
may-have-reached attempt.

The immutable provider route is
`http://v2.open.venus.oa.com/llmproxy/v1/responses`; the only model is
`gpt-5.6-sol`. The default 16-attempt policy uses a 200,000-byte conservative
input-token upper bound and a 40,000 output-token ceiling. Pricing authority
`iWiki 4020336897 v54 (2026-08-14)` gives a worst-case reservation of USD 2.45
per attempt and USD 39.20 per job. Missing or contradictory usage never releases
the reservation.

## Provider-free mock check

Use a deterministic local mock URL and pass `--provider-free-mock`. That flag is
the only way the CLI admits a target other than the fixed Venus base. The mock
must accept `/llmproxy/v1/responses`. The client and upstream token files are
read-only secret references; their values are never written to stdout or the
ledger.

```text
python3 scripts/pilot/agent-runtime-development-proxy.py \
  --provider-free-mock --target http://mock-upstream:9000/llmproxy/v1 \
  --job-id cup-cup-033-development-1 \
  --ledger /evidence/job-ledger.jsonl \
  --total-ledger /evidence/development-total-ledger.jsonl \
  --client-token-file /run/secrets/client-token \
  --upstream-token-file /run/secrets/upstream-token
```

Readiness is `GET /healthz`. The terminal ledger row is written only after the
listener and bounded request threads stop. The ledger contains request byte
counts, token ceilings, reservations, usage/cost settlements, unresolved
reservations, attempt count, and listener absence; it contains no request body
or HTTP header.

## Colima network shape

Build `scripts/pilot/agent_runtime/development_proxy.Dockerfile`, record the
resulting image ID/digest, and run it as UID/GID `65532:65532` with a read-only
root, `/tmp` tmpfs, a writable evidence mount, and read-only Docker secret
mounts. Attach the Proxy to the fresh job-private `--internal` network and to a
separately created egress network. Attach the Agent only to the internal
network. Do not place a provider token in the Agent container, its mounts, its
logs, or its receipts.

The Agent uses `http://<proxy-container-name>:8080/v1`, model `gpt-5.6-sol`, and
the job-private client token. Keep the total ledger common to all Development
jobs so the 50-job/USD-1000 gates are cumulative. Stop and preserve the ledger
if trusted usage is missing or if the pre-dispatch reservation is denied.
