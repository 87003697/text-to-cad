# Browser Sidecar prototype handoff

## Verdict

**ADOPT the architecture for a production spec.** The prototype answered the
feasibility question: an outer-owned, digest-identified OCI Sidecar can serve
the complete CAD Viewer and formal residual renderer to a browser-less nested
client through `playwright.connect()`, while Docker owns isolation and terminal
cleanup.

This branch is primary-source prototype evidence, not production code. Do not
merge these prototype files into `develop` as the implementation.

## Exact tested artifacts

| Artifact | Digest |
| --- | --- |
| Official Playwright 1.60.0 noble `linux/amd64` child | `sha256:83192064c7510f7ee73dd63dc5f22a5e01a92c81a2e6a9c715d9e3fe55471fd9` |
| Sidecar | `sha256:c61318789e67cbac0ef1d4b0b91b25b158070cee5c516e296d9661097fa980fb` |
| Sealed browser-less Agent client | `sha256:d6af75274aebcdac805a3247af557a84740cf0a053c2575bca9faf8ebbafcd77` |
| Same-Colima legacy parity derivative | `sha256:10335f887a051749c3074e7ec28628e9278d309e0a09cbf9f2d72efc78c14d95` |

The final full run used Colima profile `browser-sidecar-prototype`:
`linux/amd64`, 2 CPU, 4 GiB memory, 20 GiB disk. The default Colima profile was
not modified.

## P0-P3 result

| Gate | Result | Key evidence |
| --- | --- | --- |
| P0 provisioning | PASS | Fixed base child digest; Playwright 1.60.0; Chromium revision 1223 / 148.0.7778.96; non-root `pwuser`; final image IDs above. |
| P1 lifecycle/isolation | PASS | `ReadonlyRootfs=true`; `Mounts=[]`; internal network; external fetch blocked; no source aliases; Agent browser inventory empty; bounded CPU/memory/PIDs/shm/tmpfs; Sidecar `closing/SIGTERM`, exit 0, no terminal residue. |
| P2 Render Programs | PASS | One persistent `playwright.connect()` ran probe, complete Viewer, and residual in three fresh contexts/pages. Viewer loaded `cube.stl`; screenshot SHA-256 `3fe89234...2c45d`. Residual was 44,681 bytes, SHA-256 `21323e30...f0f9de`, with the fixed eight-view order. |
| P2 same-Colima parity | PASS | Current-style in-process launch and remote Sidecar emitted identical raw PNG byte count/hash, profile hash, and view order. |
| P3 concurrency | PASS | Both clients reached an active hold with only one Node process each. Cancelling job A produced `Browser closed`; job B remained running and completed with only its own cookie. Both Sidecars terminated exit 0; zero prototype containers/networks remained. |

The exact structured summary is in `evidence-summary.json`. The full generated
receipt from the successful single-command run was
`/tmp/browser-sidecar-prototype-evidence-final-r4/evidence.json`, SHA-256
`2045193de556d8db64f579e58ce5f784e132e7817398f6d8b1394e7614e6856e`,
and recorded 70 terminal operations. It deliberately contains command-level
local runtime facts and is not part of a production public evidence schema.

The durable Viewer fixture lives at
`models/prototypes/browser_sidecar_cube.stl`, following the repository artifact
layout and LFS policy.

## Reproduce

With the dedicated profile running, build and run everything:

```sh
python3 packages/meshshot/prototypes/browser_sidecar/harness.py \
  --docker-host unix:///Users/zhiyuanma/.colima/browser-sidecar-prototype/docker.sock \
  --evidence-dir /tmp/browser-sidecar-prototype-evidence
```

To rerun P0-P3 against already-built exact local images without pulling or
rebuilding, add `--skip-build`.

## Failures that improved the answer

1. A 30-second Viewer screenshot timeout was too short under x86 QEMU. The
   sealed program now uses a bounded 120-second timeout.
2. Separate clients obscured the one-job lifecycle and could observe a stale
   browser. The accepted proof uses one persistent Playwright connection with
   a fresh context/page per Render Program.
3. The first automated concurrency receipt cancelled before job A had reached
   its hold and attempted to remove its network while the client was attached.
   It truthfully returned `REJECT`. The harness now waits for both sealed
   `hold-ready` events and removes the cancelled network after the client exits.
   The successor receipt returned `ADOPT` with zero residue.

## Production constraints learned

- Keep one stable Playwright connection for the job and create/close a fresh
  context and page for each registered Render Program. Do not expose a generic
  URL, JavaScript, executable, environment, or browser-argument interface.
- Pre-provision the image and require its immutable digest. Runtime pulls and
  browser downloads remain closed.
- The prototype uses Chromium `--no-sandbox` inside a non-root, no-egress,
  capability-dropped, read-only OCI container because only trusted baked pages
  are rendered. The production spec must either accept this exact trust model
  or separately prove Chromium's internal sandbox under the target runtime; it
  must not silently change the threat model.
- The sealed client image is derived from the fixed Playwright base and deletes
  browser paths from its visible root. Production should use a truly
  browser-less client base to reduce artifact size; the runtime proof here is
  that the Agent can see and execute no browser path and owns no browser process.
- The Viewer screenshot is evidence of UI availability, not a cross-run pixel
  golden. Exact parity applies to the formal residual output.
- The residual parity experiment compares the browser PNG bytes before the
  unchanged Python/Pillow canonical re-encode. Identical input bytes imply the
  unchanged canonical post-processing receives identical data; the production
  migration should still add an end-to-end public-API parity test.

## Not run and next boundary

No CVM image provisioning, CVM capability probe, provider/model request, push,
merge, tracker mutation, or production runtime edit was performed. The next
step is HITL approval, then `/to-spec` and `/to-tickets`. CVM work must wait for
a reviewed repository-owned fixed image provisioning interface; do not invent
raw transfer or arbitrary SSH execution.
