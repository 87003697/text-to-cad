# Implementation Handoff: CVM Browser Sidecar fixed provision/probe path

## 对话 Transcript

`/Users/zhiyuanma/.codex/sessions/2026/08/13/rollout-2026-08-13T12-37-10-019ff968-ea54-7c60-b3fc-f08a022322f9.jsonl`

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
11. A third authorized handle again ended with bounded
    `errorCheck=image-attestation` after image load; transfer cleanup and abort
    both proved absence. R8 used only that structured receipt and did not inspect
    raw logs or either old handle. RED
    `794dd002bdb275354a5ffec1730f36623e2fdb7e` proved all 12 sidecar/client
    field failures collapsed to the generic check. GREEN
    `2a65efba2300b42487db1ae05e8b93a7c43d0db5` adds one portable four-field
    inspect projection and preserves exact role/field checks end to end.
12. R8 Standards review found that `_inspect_image` translated only existing
    `ProbeError`; command-launch `OSError` and unexpected parser exceptions
    could still escape local prepare before state. R9 RED
    `b8f8fd7aed355fc3e83b47540dc82087b964a1ff` proved four public CLI escapes
    across sidecar/client access and parse boundaries. R9 GREEN
    `874ca5e2300241d641150e14b2a9d1db29a7c525` maps them to the existing exact
    role checks without changing lifecycle or receipts.
13. A new authorized handle at reviewed `df6951ecc51ab71e41160d4bcdfb9ed6a54602aa`
    ended with exact `errorCheck=sidecar-inspect-access`; transfer cleanup and
    abort both proved absence. R10 used only those structured facts and did not
    contact CVM, inspect raw logs, or touch any terminal handle. Its RED test
    models an older Docker CLI that rejects the composite `json` template while
    accepting direct fields plus `index`. RED
    `bba5365be466f7f0d46b807ac0bc1ac8efc4051b` failed in 0.003 seconds with
    exact `sidecar-inspect-access`; GREEN
    `209cf6b10e40e2e257b8ce7b4d38c2bec4b44650` replaces only that projection
    and parser. Lifecycle, receipts, exact identity checks, and no-retry remain
    unchanged.
14. R10 passed independent Spec and Standards review after two docs-only
    contract corrections; the deployed clean workflow SHA was
    `c4f1e8385a40043a4147e2b108d7a6f693d63051`. A fresh authorized handle
    `cvmsp-b1ff1da80d7ed0006ff13fff` still terminated at exact
    `sidecar-inspect-access`. Its nonce-scoped abort reports no errors and
    proves the transfer archive, incoming directory, and prepare receipt are
    absent. The handle is terminal with `retryAllowed:false`; `probe` was not
    dispatched. This falsifies the hypothesis that the composite `json`
    template was the only CVM incompatibility. No raw remote log or terminal
    handle was inspected or mutated.

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
| `794dd002bdb275354a5ffec1730f36623e2fdb7e` R8 RED | remote-provision matrix | Sidecar/client inspect access, format, ID, platform, revision, and receipt failures all collapsed to `image-attestation`. |
| `2a65efba2300b42487db1ae05e8b93a7c43d0db5` R8 GREEN | skill, module, tests | Fixed four-field inspect projection; strict compact parse and closed role-specific failure preservation through public receipts. |
| `b8f8fd7aed355fc3e83b47540dc82087b964a1ff` R9 RED | public prepare CLI matrix | Sidecar/client command-launch and compact-parser unexpected exceptions returned rc1/raw traceback. |
| `874ca5e2300241d641150e14b2a9d1db29a7c525` R9 GREEN | skill, module | Bound unexpected run exceptions to role `inspect-access` and parser exceptions to role `inspect-format`. |
| `bba5365be466f7f0d46b807ac0bc1ac8efc4051b` R10 RED | remote-provision compatibility seam | An older-Docker fake rejected the composite `json` template and reproduced exact `sidecar-inspect-access`. |
| `209cf6b10e40e2e257b8ce7b4d38c2bec4b44650` R10 GREEN | module, tests | Direct-field/index tab projection works without the template `json` helper while retaining strict role checks. |
| `c4f1e8385a40043a4147e2b108d7a6f693d63051` reviewed R10 workflow | skill contract/docs | Final clean deployed SHA; documents the TAB projection and closed parser checks exactly. |

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
- Loaded-image inspection requests only one fixed tab-delimited projection of
  ID, OS, architecture, and OCI revision label using direct fields plus
  `index`; it never requests or accepts full daemon inspect JSON and does not
  depend on the Docker template `json` helper. Sidecar and client independently
  classify inspect access, compact format, ID, platform, revision, and strict
  prepare-receipt failures.
  An unexpected implementation escape uses the sole fixed
  `image-attestation-unexpected` fallback; existing role-specific `ProbeError`
  checks are never blanket-wrapped.
- The same role checks close local prepare before state: launch/socket/
  permission exceptions become role `inspect-access`, and unexpected
  TAB-delimited projection parser exceptions become role `inspect-format`.
  Public stderr is fixed and contains no traceback, path, errno, or source
  exception text.
- Prepare cleanup failure still dominates as `prepare-cleanup-absence`. When
  cleanup proves absence, existing fixed checks remain unchanged and every
  non-`ProbeError` becomes `prepare-operation`; no traceback/path/errno/message
  crosses the public CLI boundary.

### Prototype and execution evidence

- The local dedicated Colima prototype is independently reviewed **ADOPT** at
  runtime/image source `1abe4c97929906b5c0b28b0f3f38857bd923952f`.
- Exact images are Sidecar
  `sha256:22ff2413ffd9dcdb5f62e5dbb2c6e46d6b4e98f0e45dc4698f80eb8f06b146f1`
  and sealed Agent
  `sha256:a2dae48401a6918a15e68a97c4c0290ba6a58ec47a3448498aec12885be46373`.
- R8 evidence is
  `/tmp/browser-sidecar-prototype-evidence-r2-r8/evidence.json`, SHA-256
  `dc7b29c4109e3ae64c5f1e610363c729e8b7a791b05563f23b6964129142305e`:
  30/30 predicates true, 134 operations, and 13/13 exact resources absent.
- Visual proof: the real STEP Viewer changed Orthographic to Perspective;
  screenshot was 60,743 bytes with SHA-256
  `ef40fdbc99f49ecda27df4ec3a6352d5349d217baf922acaafb4e72fc054a2c9`.
  The unchanged public `meshshot.render_residual_preview` local baseline and
  remote Sidecar result matched in PNG bytes/hash, mode, dimensions, profile,
  view order, variant, and evidence.
- CVM execution did **not** reach this runtime. Every CVM attempt stopped in
  provisioning before the sealed probe; no Viewer screenshot, Chromium
  process, model-provider call, or paid pilot was produced on CVM.

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

R8 final validation:

- RED remote-provision matrix → **12 expected failures**.
- Focused suite → **35 tests, OK**.
- Global gate with the project virtualenv and local loopback permission →
  **186 tests, OK**, explicit exit 0 from the continued process.
- `python3 -m py_compile` for the module and focused test → exit 0.
- `git diff --check` and staged diff check → exit 0.
- External/CVM operations: **zero**; incremental spend: **$0**.

R9 final validation:

- RED public CLI matrix → **4 expected failures**.
- Focused suite → **36 tests, OK**.
- Global gate with the project virtualenv and local loopback permission →
  **187 tests, OK**, explicit exit 0 from the continued process.
- `python3 -m py_compile` for the module and focused test → exit 0.
- `git diff --check` and staged diff check → exit 0.
- External/CVM operations: **zero**; incremental spend: **$0**.

R10 final validation:

- RED older-Docker compatibility seam → **1 expected failure**, exact
  `sidecar-inspect-access`, 0.003 seconds.
- Focused suite → **37 tests, OK**.
- Global gate with the project virtualenv and local loopback permission →
  **188 tests, OK**, explicit exit 0.
- `python3 -m py_compile` for the module and focused test → exit 0.
- `git diff --check` and staged diff check → exit 0.
- The first restricted global run was environment-only FAIL: eight loopback
  bind permission errors plus two missing lightweight-worktree dependencies;
  the authorized same-code rerun above passed.
- Independent Spec and Standards reviews passed on implementation
  `209cf6b10e40e2e257b8ce7b4d38c2bec4b44650` and final clean docs/workflow
  `c4f1e8385a40043a4147e2b108d7a6f693d63051`.
- `cvm-push` deployed `c4f1e838...` successfully and verified the remote
  runtime contract. CVM Git base was `no-git` and is not the deployment
  identity.
- Fresh prepare handle `cvmsp-b1ff1da80d7ed0006ff13fff` bound archive
  1,084,572,160 bytes, SHA-256
  `2ae8ec5106f642aa38cbb20f649c8dce17c885eb6eb22ea98dbd66dffd357e17`.
- Its only provision ended terminal `sidecar-inspect-access`; retry is false.
  Nonce-scoped abort had no errors and proved all three transfer artifacts
  absent. Probe dispatch was **NOT_RUN**.

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

- CVM capability probe is **NOT_RUN**. No CVM Chromium, Viewer, eight-view
  render, paid model, or full pilot evidence exists.
- The latest two exact attempts both failed after `docker image load` at
  `sidecar-inspect-access`, once with the composite JSON template and once with
  the direct TAB projection. Template composition is therefore ruled out as
  the sole cause.
- The closed receipt does not distinguish a missing/unaddressable loaded image,
  a failure of even the simplest ID-only inspect, or Docker client/daemon
  access/timeout. Do not guess among them and do not read raw remote logs.
- Provision may have retained the loaded image IDs by design. Their deletion is
  a separate destructive action and remains unauthorized.
- Every handle below is terminal, `retryAllowed:false`, and must never be
  resubmitted or adopted:

| handle | deployed workflow | terminal check | transfer absence |
|---|---|---|---|
| `cvmsp-400bce2bc3943c2715fdc8ce` | `97aef65d...` | `workflow` | proved |
| `cvmsp-475cb7351c40347e39b2e337` | `e7b88118...` | `image-attestation` | proved |
| `cvmsp-f09440db5fc58c4c7286e24d` | `e6ffdf77...` | `image-attestation` | proved |
| `cvmsp-cc4a136cdb7012a4ea37412f` | `df6951ec...` | `sidecar-inspect-access` | proved |
| `cvmsp-b1ff1da80d7ed0006ff13fff` | `c4f1e838...` | `sidecar-inspect-access` | proved |

## 下一步

1. Open one narrow diagnostic ticket at the fixed public remote-provision seam.
   The first fixed command should inspect only `{{.Id}}`; later OS,
   architecture, and revision projections run only after ID access succeeds.
   Persist one bounded check for ID-only command access versus later field
   format/identity failure; never persist Docker output, stderr, path, errno,
   socket, or daemon JSON.
2. Add deterministic RED coverage for: loaded ID not addressable, ID-only
   inspect command nonzero, timeout, OS/architecture/revision field failure,
   first-failure preservation, and transfer cleanup. Keep the public
   prepare/provision/probe interface unchanged.
3. Require one new clean SHA, focused/global gates, and independent Spec plus
   Standards PASS. Only then may the parent authorize one fresh handle. Do not
   touch any tabled handle or delete retained images.
4. If and only if provision succeeds, dispatch `probe` exactly once on that
   same fresh handle. A successful provision without probe is still not a CVM
   capability result.
5. Keep the report distinction explicit: Colima R8 is real local ADOPT with a
   visual Viewer/render result; CVM has only deployment/provision receipts and
   no runtime visual or paid-flow result.
