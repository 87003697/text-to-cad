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
counts, token ceilings, reservations, usage/cost-upper-bound settlements,
unresolved reservations, attempt count, and listener absence; it contains no
request body or HTTP header. Responses token usage cannot distinguish ordinary
uncached input from cache-write input, so settled uncached input is charged at
the more expensive applicable rate. `actualUsd` remains null with an explicit
missing-telemetry reason.

## Colima network shape

Do not pull or build a second image. Reuse the imported Development Agent image
`sha256:a64ae96f4703bb8dfdbce1159106f606f1f00e1bf05991fa4bcabe27a0bfedc2`
and override its entrypoint with `/usr/bin/python3.12`. Bind the reviewed proxy
scripts read-only, run as UID/GID `65532:65532` with a read-only root and `/tmp`
tmpfs, and use job-scoped named volumes for evidence and secret references.
Seed the secret volume over container stdin, then mount it read-only only in the
Proxy. Attach the Proxy to the fresh job-private `--internal` network and a
separate egress network. Attach the Agent only to the internal network. Do not
place a provider token in the Agent container, its mounts, logs, or receipts.

The Agent uses `http://<proxy-container-name>:8080/v1`, model `gpt-5.6-sol`, and
the job-private client token. The Development supervisor seeds only this config
into the Agent HOME and requires the Agent's sole network to be the pre-created
internal network. Its terminal points accounting to the external proxy ledger;
it does not claim a zero dispatch count once the capability exists. Keep the
total ledger common to all Development jobs so the 50-job/USD-1000 gates are
cumulative. Stop and preserve the ledger if trusted token usage is missing or
if the pre-dispatch reservation is denied.

Run the complete zero-provider Colima launcher with fresh paths:

```text
python3 scripts/pilot/agent-runtime-development-proxy-conformance.py \
  --host-stage /Users/<user>/.../fresh-sai010-stage \
  --output outputs/agent-runtime/cup_cup_033/fresh-sai010-conformance
```

It reuses the imported image, creates fresh exact internal/egress networks,
runs a deterministic mock and dual-homed Proxy, verifies 48 attempts plus the
49th denial, wrong token/route/model denial, timeout reservation retention, and
terminal cleanup/absence. It never resolves or calls Venus. Failed output paths
are retained and never overwritten.
