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
6. R2 re-review narrowed three remaining probe-boundary failures. R3 RED
   `a531b507` proved cross-handle probe success spoofing, probe claims before
   deployment/disk gates, and ambiguous begin ownership after lost stdout. R3
   GREEN `343a412e` closes only those three seams; it did not contact CVM.
7. The first authorized provision attempt later ended with only the structured
   classification `errorCheck=workflow`; its nonce-scoped abort proved transfer
   absence. R4 deliberately did not inspect raw logs, contact CVM, or touch that
   terminal handle. RED `ec90dbee723b6f6d94728f5484ed6b8e1756c98b`
   reproduced the missing pre-transfer capacity/Docker gates and missing bounded
   remote failure receipt. GREEN
   `1ee713d0e00e2b0510c7cce577786cccb9801491` closes only those seams.
8. A later authorized handle reached every preflight and transfer-cleanup gate
   but terminated with the bounded `errorCheck=image-attestation`; its abort
   also proved transfer absence. R5 used only those structured facts and did
   not inspect raw logs, contact CVM, or touch either terminal handle. RED
   `0bf90944374941466dbe7ca3a4efea39d652a77b` proved that hashing Docker's
   engine-dependent `inspect Config` display was non-portable and that archive
   manifests were not bound to the exact config blobs. GREEN
   `3f6274d1090fc53c5023e20b7e61bcc89f2db279` closes only that portability seam.
9. R5 Standards review rejected its new tar parser as unnecessary attack
   surface because no public archive input exists. R6 RED
   `66ad21a1c17b84858d5870d525af1357526ef59e` proved the desired opaque archive
   boundary and exposed raw prepare-cleanup failure. R6 GREEN
   `faa23e5c8a482a4ee1c017cca9e92f028c90c677` deletes the parser, preserves the
   ID-derived config identity, and adds bounded exact prepare cleanup. It did
   not contact CVM or touch either terminal handle.
10. R6 Standards review found one remaining prepare escape: after successful
    cleanup, a non-`ProbeError` was bare-rethrown. R7 RED
    `7ab9c5878605aaa9ae9e58441a7c73fafd8eab5b` injected a post-save filesystem
    failure and captured its raw rc1 traceback boundary. R7 GREEN
    `ce3143e4bc30c222b400aa5637edc8df48619169` preserves existing bounded
    `ProbeError` checks but replaces every other successfully-cleaned exception
    with fixed `prepare-operation`. No external operation occurred.

## Session 产出

### Commits 与文件改动

| commit/状态 | 文件 | 说明 |
|---|---|---|
| `98fd40f4ac4d8901b58107327df06edf565a8b3e` RED | `tests/python/global/test_cvm_sidecar_probe.py` | Public prepare seam; failed because the fixed wrapper did not exist. |
| `a1d04d8514a62670e79c995f58d5fb2de7b1aa3d` GREEN | skill, wrapper/module, tests, ignores, AGENTS/README | Fixed archive attestation, named transfer/load, one-shot sealed probe, terminal ledger/absence evidence. |
| `f546470c` R2 RED | `tests/python/global/test_cvm_sidecar_probe.py` | Proved a failed predictable-name create could authorize cleanup of a foreign resource. |
| `92e228c0` R2 GREEN | skill, module, tests | Fresh ownership proof; exact receipt/deployment/disk gates; collision-safe best-effort terminal cleanup. |
| `a531b507` R3 RED | focused global tests | Spoofed probe success, pre-gate claim, and lost-begin ownership regressions. |
| `343a412e` R3 GREEN | skill, module, tests | Strict public probe receipt binding, pre-claim remote gates, and locally durable begin nonce. |
| `ec90dbee723b6f6d94728f5484ed6b8e1756c98b` R4 RED | focused global tests | Missing archive-aware capacity gate, Docker server gate, bounded remote failure receipt, and preserved public failure classification. |
| `1ee713d0e00e2b0510c7cce577786cccb9801491` R4 GREEN | skill, module, tests | Pre-transfer archive-capacity and fixed Docker gates; bounded persisted failure receipts and strict public receipt/SSH binding. |
| `0bf90944374941466dbe7ca3a4efea39d652a77b` R5 RED | focused global tests | Same ID produced different config hashes across inspect display shapes; invalid archive config manifests were accepted. |
| `3f6274d1090fc53c5023e20b7e61bcc89f2db279` R5 GREEN | skill, module, tests | ID-derived immutable config digest; safe read-only docker-save manifest/config-blob binding and fresh-state cleanup. |
| `66ad21a1c17b84858d5870d525af1357526ef59e` R6 RED | focused global tests | Opaque fixed-save bytes were rejected; cleanup failure leaked an unbounded exception. Local archive mutation already failed before transfer. |
| `faa23e5c8a482a4ee1c017cca9e92f028c90c677` R6 GREEN | skill, module, tests | Removed tar parser; retained whole-archive attestation and authoritative post-load identity proof; bounded exact prepare cleanup. |
| `7ab9c5878605aaa9ae9e58441a7c73fafd8eab5b` R7 RED | public CLI test | Injected post-save `os.replace` failure escaped as rc1/raw traceback despite successful cleanup. |
| `ce3143e4bc30c222b400aa5637edc8df48619169` R7 GREEN | skill, module | Preserve fixed `ProbeError`; map every other successfully-cleaned prepare exception to fixed `prepare-operation`. |

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
- `configSha256` means exactly the 64-hex config digest in the immutable image
  ID; it is never recomputed from Docker's version-dependent `inspect Config`
  display. This clarified semantic retains receipt schema v1. The fixed local
  Docker save is intentionally opaque: callers cannot supply archive bytes or
  paths, the whole archive bytes/size/SHA are attested and rechecked locally
  before transfer, and remote Docker load followed by exact ID/platform/
  revision verification is the authoritative config proof.
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
  verification, a fixed accessible `linux/amd64` Docker server check, and free
  disk of at least 3 GiB plus the exact attested archive size. The existing
  3 GiB disk gate runs again after transfer and before image load.
- A fresh 128-bit nonce in the exact `remote-begin` receipt proves ownership.
  Failed begin cannot abort or adopt an existing predictable handle.
- Runtime cleanup resolves each exact Docker ID and verifies both handle and
  owner labels before deletion. Name collision is never deletion authority.
- Public provision accepts success only when every prepared image/archive and
  terminal/no-retry field matches exactly. Probe SSH/malformed/lost-output and
  cleanup-timeout paths persist terminal failure and continue all safe cleanup.
- Public probe first revalidates the local provision against prepare, then
  accepts remote success only with SSH exit 0 and exact handle, image roles/
  IDs/platform/config/revisions, workflow file hashes, request/result,
  resource ledger, terminal state, absence proof, retained IDs, and no-retry
  operation. Missing, mismatched, cross-handle, or spoofed success is terminal.
- Remote probe re-runs deployed module/wrapper hashes and the current 3 GiB
  disk gate before its one-shot claim or any Docker resource creation.
- Provision generates and records the ownership nonce locally before begin,
  passes it into begin, and uses that same nonce for an exact abort if stdout is
  lost. A foreign predictable handle cannot satisfy the nonce receipt.
- The exact two loaded image IDs are intentionally retained as provisioned
  artifacts. Removing them is a separate destructive authorization boundary.
- Remote provision failures after ownership is proven persist and emit a fixed
  receipt classified as exactly one bounded stage: prepare receipt, archive
  hash/size, post-transfer disk, image load, image attestation, transfer
  cleanup, or deployed workflow hash. Public provision accepts such a failure
  only with SSH exit 1, preserves its exact classification, performs the same
  nonce-scoped abort, and never publishes raw stderr/path/errno as evidence.
- Prepare cleanup failure still dominates as `prepare-cleanup-absence`. When
  cleanup proves absence, existing fixed checks remain unchanged and every
  non-`ProbeError` becomes `prepare-operation`; no traceback/path/errno/message
  crosses the public CLI boundary.

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

R3 final validation:

- Focused suite → **22 tests, OK**.
- Global gate with the project virtualenv and local loopback permission →
  **173 tests, OK**.
- `python3 -m py_compile` for the module and focused test → exit 0.
- `git diff --check` and staged diff check → exit 0.

R4 final validation:

- Four new RED seams failed independently before implementation.
- Focused suite → **28 tests, OK**.
- Global gate with the project virtualenv and local loopback permission →
  **179 tests, OK**.
- `python3 -m py_compile` for the module and focused test → exit 0.
- `git diff --check` and staged diff check → exit 0.
- External/CVM operations: **zero**; incremental spend: **$0**.

R5 final validation:

- RED differential/manifest suite → **2 tests with 5 expected failures**.
- Focused suite → **30 tests, OK**.
- Global gate with the project virtualenv and local loopback permission →
  **181 tests, OK**.
- `python3 -m py_compile` for the module and focused test → exit 0.
- `git diff --check` and staged diff check → exit 0.
- External/CVM operations: **zero**; incremental spend: **$0**.

R6 final validation:

- RED focused seams → **3 tests: 2 expected failures, 1 existing pass**.
- Focused suite → **33 tests, OK**.
- Global gate with the project virtualenv and local loopback permission →
  **184 tests, OK**.
- `python3 -m py_compile` for the module and focused test → exit 0.
- `git diff --check` and staged diff check → exit 0.
- R6 production diff versus its RED commit: **71 insertions, 150 deletions**.
- External/CVM operations: **zero**; incremental spend: **$0**.

R7 final validation:

- RED public CLI seam → **1 expected failure**.
- Focused suite → **34 tests, OK**.
- Global gate with the project virtualenv and local loopback permission →
  **185 tests, OK**.
- `python3 -m py_compile` for the module and focused test → exit 0.
- `git diff --check` and staged diff check → exit 0.
- External/CVM operations: **zero**; incremental spend: **$0**.

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
- R4 performed no CVM code push, provision, or probe and spent `$0`. The prior
  failed handle remains terminal with structured abort/transfer-absence proof;
  R4 neither inspected nor mutated it.
- Independent Standards/Spec review and parent integration remain parent-owned.

R4 adds no new external attempt. Its clean review range is
`97aef65d..1ee713d0e00e2b0510c7cce577786cccb9801491`; the previously failed
handle remains terminal and untouched.

R5 adds no new external attempt. Its clean implementation review range is
`e7b881186feadde7d6cb9e8b5df48730f84cb06a..3f6274d1090fc53c5023e20b7e61bcc89f2db279`;
both previously failed handles remain terminal and untouched.

R6 adds no new external attempt. Its clean implementation review range is
`1da059f0a4ee95f49644a3deb2d5cba3c4db6498..faa23e5c8a482a4ee1c017cca9e92f028c90c677`;
both previously failed handles remain terminal and untouched.

R7 adds no new external attempt. Its clean implementation review range is
`367681a5cb5cb98376f0b5d388c076ee54d61c6b..ce3143e4bc30c222b400aa5637edc8df48619169`;
both previously failed handles remain terminal and untouched.

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
