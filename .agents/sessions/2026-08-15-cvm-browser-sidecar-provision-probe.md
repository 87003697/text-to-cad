# Implementation Handoff: CVM Browser Sidecar fixed provision/probe path

## 对话 Transcript

`/Users/zhiyuanma/.codex/sessions/2026/08/15/rollout-2026-08-15T02-54-15-01a0019f-f3ee-7d22-a564-dbd24bf4660c.jsonl`

## 前序 Session

- `/Users/zhiyuanma/Desktop/codes/text-to-cad/.agents/sessions/2026-08-14-browser-sidecar-prototype-handoff.md`
  — Browser Sidecar P0-P4 scope, accepted runtime boundary, and CVM capability
  probe gate.

## 相关 Plan

- `/Users/zhiyuanma/Desktop/codes/text-to-cad/.agents/plans/cvm-sync-and-pilot-review.md`
  — project-scoped CVM skill/wrapper convention and never-`--delete` boundary.

## 任务目的

Add the smallest repository-approved path that can prepare two exact reviewed
Browser Sidecar OCI image IDs locally, provision them on CVM without a runtime
pull, and run one sealed provider-free capability probe. Provision and probe
remain separate authorized operations. The path must not expose arbitrary
commands, paths, environment, URLs, Docker options, or request bodies; must not
retry/resubmit a handle; and must prove exact owned-resource cleanup.

This implementation ticket did not contact or write CVM, run a provider/pilot,
spend money, push Git, merge, mutate a tracker, or remove retained images.

## 执行内容

1. Created isolated worktree
   `/private/tmp/text-to-cad-cvm-sidecar-provision-20260815`, branch
   `codex/cvm-sidecar-provision-20260815`, baseline
   `9c5b7ea39030a013023a2f06c83b9b869a394861`.
2. Fixed three public seams in `.claude/skills/cvm-sidecar-probe/SKILL.md`:
   local `prepare`, external `provision`, and the only one-shot external
   `probe`. Remote lifecycle subcommands are not accepted by the shell wrapper.
3. Recorded the deterministic RED test in
   `98fd40f4ac4d8901b58107327df06edf565a8b3e`.
4. Implemented and committed GREEN in
   `a1d04d8514a62670e79c995f58d5fb2de7b1aa3d`.
5. After independent review rejected the first GREEN, recorded the bounded R2
   RED in `f546470c` and the R2 GREEN in `92e228c0`. R2 adds fresh ownership
   nonces, exact-ID plus dual-label cleanup authority, strict receipt binding,
   durable probe failure receipts, deployed wrapper/module SHA-256 gates, and
   the mandatory 3 GiB remote disk gate. It did not contact CVM.

## Session 产出

### Commits 与文件改动

| commit/状态 | 文件 | 说明 |
|---|---|---|
| `98fd40f4ac4d8901b58107327df06edf565a8b3e` RED | `tests/python/global/test_cvm_sidecar_probe.py` | Public prepare seam; failed because the fixed wrapper did not exist. |
| `a1d04d8514a62670e79c995f58d5fb2de7b1aa3d` GREEN | skill, wrapper/module, tests, ignores, AGENTS/README | Fixed archive attestation, named transfer/load, one-shot sealed probe, terminal ledger/absence evidence. |
| `f546470c` R2 RED | `tests/python/global/test_cvm_sidecar_probe.py` | Proved a failed predictable-name create could authorize cleanup of a foreign resource. |
| `92e228c0` R2 GREEN | skill, module, tests | Fresh ownership proof; exact receipt/deployment/disk gates; collision-safe best-effort terminal cleanup. |

### 核心行为

- `prepare` accepts only one 40-hex image-source revision plus two exact
  `sha256:<64 hex>` image IDs. Both images must inspect as `linux/amd64`, their
  IDs must remain exact, and both configs must contain
  `org.opencontainers.image.revision` equal to the supplied image-source SHA.
- The prepare receipt separately binds `imageSourceRevision` and the clean Git
  HEAD of the provisioning wrapper as `workflowSourceRevision`; they are
  intentionally different identities.
- The archive is a fixed `docker image save` of exactly Sidecar then sealed
  client. Receipt stores archive bytes/SHA-256 and each full config SHA-256.
- `provision` creates a local and remote one-shot claim before transfer. Its
  wrapper performs one explicit no-delete rsync to a computed handle path,
  remote-verifies hash/size before `docker image load`, then re-inspects both
  IDs/platform/config. Transfer archive/receipt/directory must be absent before
  a success receipt is written.
- If remote begin occurred but transfer/finalize failed, one registered abort
  operation removes only the exact transfer files/directory and records an
  absence receipt. The handle remains terminal and cannot be retried.
- `probe` uses the fixed structured request
  `meshshot.browser-sidecar.render-request/2` / `program:"probe"` / empty
  payload. It starts an internal network plus exact Sidecar/client containers
  with `--pull=never`, read-only root, dropped capabilities, no-new-privileges,
  fixed memory/CPU/PID/tmpfs bounds, and no mounts.
- The client is created first, immediately registered by exact name/ID, then
  started/attached with the fixed stdin request. Sidecar readiness/result,
  terminal SIGTERM/exit 0, reverse-order cleanup, and label-filtered container
  and network absence are fail-closed predicates.
- Receipts reject duplicate JSON keys. Durable failures contain fixed
  operation/check classifications, not raw exception/path/errno strings.
- Docker, SSH, rsync, image save/load, readiness, and probe operations all have
  bounded timeouts.
- Remote state is created only after exact deployed module/wrapper SHA-256
  verification and a free-disk result of at least 3 GiB. The same checks run
  again before image load.
- A fresh 128-bit nonce in the exact `remote-begin` receipt proves ownership.
  Failed begin cannot abort or adopt an existing predictable handle.
- Runtime cleanup resolves each exact Docker ID and verifies both handle and
  owner labels before deletion. Name collision is never deletion authority.
- Public provision accepts success only when every prepared image/archive and
  terminal/no-retry field matches exactly. Probe SSH/malformed/lost-output and
  cleanup-timeout paths persist terminal failure and continue all safe cleanup.
- The exact two loaded image IDs are intentionally retained as provisioned
  artifacts. Removing them is a separate destructive authorization boundary.

### 验证结果

- RED:
  `python3 -m unittest tests.python.global.test_cvm_sidecar_probe` → 1 error,
  exit 1, fixed wrapper absent.
- Focused GREEN: same command → **11 tests, OK**.
- Repository global gate:
  `PYTHON_BIN=/Users/zhiyuanma/Desktop/codes/text-to-cad/.venv/bin/python scripts/test/test-global.sh`
  with local loopback permitted → **162 tests, OK**.
- `python3 -m compileall -q scripts/pilot/cvm_sidecar_probe.py tests/python/global/test_cvm_sidecar_probe.py`
  → exit 0.
- `git diff --check` and staged diff check → exit 0.
- Final implementation commit status was clean on
  `codex/cvm-sidecar-provision-20260815`.

R2 final validation:

- Focused suite → **17 tests, OK**.
- Global gate with the project virtualenv and local loopback permission →
  **168 tests, OK**.
- `python3 -m py_compile` for the module and focused test → exit 0.
- `git diff --check` and staged diff check → exit 0.

The first restricted global run was not accepted as product evidence: it had
eight loopback-bind permission errors and two missing-worktree-dependency
errors. It also exposed one real README compatibility assertion introduced by
this branch; that wording was fixed before the clean 162-test rerun.

## 关键决策

- Chose a project skill plus durable wrapper rather than expanding
  `$cvm-push` or using raw transport. Code deployment, image provisioning, and
  execution stay independently receipted.
- Chose `docker save`/hash/`docker load` rather than a registry because this
  shared CVM currently has no repository-approved fixed registry path.
- Chose terminal one-shot claims rather than idempotent retry. A lost transport
  response cannot accidentally create a second probe.
- Retain exact provisioned images but clean every temporary transfer/runtime
  resource. Image deletion is not implied by probe cleanup.

## 未完成事项

- The real final prototype image IDs were not available during this ticket.
  Do not run `prepare` until the prototype owner supplies both final IDs and the
  full clean image-source SHA whose label is present in both configs. The owner
  reported clean runtime commit prefix `e1a68655`; verify the full SHA and final
  rebuild receipt rather than expanding this prefix.
- No CVM code push, provision, or probe occurred. External handle count is zero
  and incremental spend is `$0`.
- Independent Standards/Spec review and parent integration remain parent-owned.

## 下一步

1. Review fixed range
   `9c5b7ea39030a013023a2f06c83b9b869a394861..HEAD`; require both axes PASS on
   the same clean SHA.
2. Obtain the prototype owner's exact final Sidecar/client IDs and full clean
   image-source SHA. Locally inspect both labels before `prepare`.
3. Deploy the reviewed workflow code once through `$cvm-push`; preserve that
   deployment receipt as the workflow-source attestation.
4. Run `prepare` locally. With the separately authorized external gate, run
   `provision <handle>`, then exactly one `probe <same-handle>`.
5. Report exact reviewed workflow SHA, image-source SHA, both image IDs/config
   hashes, archive SHA-256, handle, probe receipt, terminal operation, and
   absence proof. Never retry a failed handle.
