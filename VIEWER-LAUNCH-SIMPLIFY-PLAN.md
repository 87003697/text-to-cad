# Viewer Launch Simplification — Execution Plan

Delete this file in the final implementation commit; it is a working plan for
the `claude/viewer-launch-simplify-25tiux` branch, not durable repo guidance.

## Goal

Remove the port-rolling / server-discovery machinery from the CAD Viewer
launcher. The launcher targets **one fixed port (default 4178)**: it reuses a
compatible viewer already on that port, starts one if the port is free, and
errors clearly if the port is held by something else — at which point the
calling agent picks its own `--port`. The knowledge that used to live in the
launcher (worktree reuse, when to use a fresh port) moves into short
instructions in `skills/cad-viewer/SKILL.md` and repo `AGENTS.md`.

## Baseline

- Branch: `claude/viewer-launch-simplify-25tiux`, based on `origin/release/0.4.0`
  (per request; note repo convention is to branch from `develop` — flag the PR
  target when opening one).
- Entry point stays `npm --prefix scripts/viewer run agent:start` →
  `viewer/scripts/start-agent-viewer-py.mjs` (venv discovery shim, unchanged) →
  `python -m server_py.start_agent_viewer`.

## Decisions log (from review thread)

1. **No port rolling.** Probe only the requested port (default 4178). Occupied
   by a non-viewer → clear error + exit 1; the agent reruns with `--port <n>`.
2. **Reuse stays in the start script**, scoped to that single port: probe
   `/__cad/server`, and if a compatible viewer answers, POST
   `/__cad/directory/activate` and print the reuse URL.
3. **Remove the Claude Preview section** from SKILL.md entirely. Do not add a
   `.claude/launch.json` (none exists today); agents figure preview out.
4. **Keep a minimal `--json`** output line `{url, port, action}` (cheap, useful
   for any future launch config).
5. **Keep** dev/serve auto-detection (`select_mode`) — it is what makes the
   same command work in both the bundled skill install and the symlinked
   `develop` checkout. Keep `cad-python.mjs` (Vite proxy also uses it) and
   `serverLifetime.mjs` (used by `vite.config.mjs`, not launcher machinery).
6. **Delete the server registry** (`server_py/registry.py`) and the
   `serverApiVersion` reuse gate — verified their only consumers are the
   launcher scan being removed and `server.py`'s self-registration.
7. **AGENTS.md gains one light rule:** threads actively changing `viewer/`,
   `packages/cadjs`, or `packages/implicitjs` should launch their own viewer on
   a fresh `--port` instead of reusing 4178, so they test their edits rather
   than a stale/stable viewer.

## Code changes

### 1. Rewrite `viewer/server_py/start_agent_viewer.py` (~165 → ~60 lines)

- CLI: `--host` (default 127.0.0.1), `--dir` (required, absolute), `--port`
  (default 4178), `--json`. **Remove** `--port-scan-limit` and the vestigial
  `--viewer-start-mode`.
- Single-port decision, no loop:
  - `probe()` returns `viewer` and `is_reusable(info)` → `activate_directory()`,
    print `CAD Viewer already running at <url>` + `CAD Viewer URL: <url>`
    (+ JSON `{"url","port","action":"reuse"}` with `--json`), exit 0.
  - `closed` → `spawn_backend(select_mode(), ...)`, print
    `Starting CAD Viewer <mode> server at <url>` + `CAD Viewer URL: <url>`
    (+ JSON `action:"start"`), wait on child.
  - `occupied` (non-viewer process, or a viewer that fails the reuse gate) →
    stderr: `Port <port> on <host> is already in use by another process. Rerun
    with --port <n> to use a different port.` Exit 1.
- Simplify `is_reusable`: `app == "cad-viewer"` and `"directory-activation"` in
  `serverFeatures` and `dynamicRoot is True`. Drop the
  `serverApiVersion >= VIEWER_SERVER_API_VERSION` check.
- Drop the `registry_mod` import and registry-first scan order.
- Drop the `CAD Viewer git: none` stdout line (nothing in the repo consumes it;
  only `CAD Viewer URL:` is the greppable contract).

### 2. Delete `viewer/server_py/registry.py`

- In `viewer/server_py/server.py` `main()`: remove `write_registry` /
  `atexit.register(...)` / `remove_registry_entry` calls and the
  `registry_mod` imports (both import branches at top of file).
- No other consumers (verified by grep; no tests reference it).

### 3. Trim `viewer/server_py/server_info.py`

- Remove `serverApiVersion` from the payload and the
  `VIEWER_SERVER_API_VERSION` constant (launcher gate was the only consumer;
  client `viewer/src` and `server_py/tests` do not read it — verified).
- Remove the optional `git` key plumbing (nothing sets it since the Node
  launcher was ported; `VIEWER_GIT` survives only in README prose).
- Update the module docstring to describe the new reuse gate
  (app + `directory-activation` + `dynamicRoot`).

### 4. Keep unchanged

- `/__cad/server` (probe) and `/__cad/directory/activate` (reuse) endpoints.
- `viewer/scripts/start-agent-viewer-py.mjs`, `cad-python.mjs`,
  `serverLifetime.mjs`, `viewerEnv.mjs`, `directoryRoot.mjs`.
- `vite.config.mjs` dev-mode backend spawning (its internal ephemeral backend
  port is invisible to callers and out of scope).

## Documentation changes

### 5. `skills/cad-viewer/SKILL.md`

- Rewrite **Start Viewer**: fixed default port 4178; `agent:start` reuses a
  compatible viewer on that port (activating the requested `--dir`) or starts
  one; if the port is busy with something else it exits with an error — rerun
  with an explicit free `--port <n>` and use the printed URL. Delete the
  "launcher owns port selection / do not probe ports" language and the
  worktree/git-identity reuse paragraph.
- Keep the `--dir` contract (absolute dir, `file=` relative to it, one URL per
  file) and the Links section, minus launcher-magic references.
- **Delete the entire "Claude Preview" section.** Keep `--json` documented in
  one sentence under Start Viewer (last stdout line beginning with `{`).
- Keep the sandbox note about `EPERM`/`EACCES` bind failures.

### 6. Repo `AGENTS.md` (CAD Viewer section)

- Replace the "launcher owns port selection, reuses a compatible live Viewer
  for the same worktree/branch" paragraph with: launcher targets port 4178,
  reuses or starts there, and errors if the port is taken — pass `--port <n>`
  yourself in that case.
- Add the light development rule (decision 7 above): use a fresh `--port` when
  the thread is actively changing viewer or viewer-adjacent package code.
- Keep: `--dir` required/absolute, `file=` relative, don't stop existing
  viewers.

### 7. `viewer/README.md`

- Update the "Local tools should not assume a fixed port" paragraph → the
  launcher now assumes 4178 unless `--port` is passed; describe
  reuse-or-start-or-error.
- Remove `VIEWER_SERVER_REGISTRY` and `VIEWER_GIT` from the environment
  variable list; update the `agent:start` script description.
- `skills/cad-viewer/references/development.md` has no launcher references
  (verified) — re-check during execution after edits.

## Tests

### 8. Add `viewer/server_py/tests/test_start_agent_viewer.py`

No existing tests cover the launcher or registry (verified). Add unit tests
with stubbed `probe`/`activate_directory`/`spawn_backend`:

- reuse path: reusable viewer on the port → activates dir, prints reuse URL,
  exit 0, no spawn.
- start path: port closed → spawns backend with chosen mode, prints start URL.
- occupied path: non-viewer on port → exit 1, error names the port and
  `--port`, no spawn, no activation.
- `is_reusable`: accepts app+feature+dynamicRoot, rejects missing feature /
  wrong app.
- `--json`: last stdout line parses to `{url, port, action}`.

Run targeted: `./.venv/bin/python -m unittest viewer.server_py.tests.test_start_agent_viewer`
(match invocation style of existing `server_py/tests` — they are run as
targeted unittest paths, not wired into `scripts/test/test-python.sh`).

## Bundle + verification (in order)

1. `scripts/dev/setup-symlinks.sh --check` — layout intact before/after.
2. `npm --prefix viewer run test` — node tests (scripts/src `.test.mjs`).
3. Targeted python unittest for the new launcher test + existing
   `server_py/tests` suites.
4. `scripts/bundle/bundle.sh` then `scripts/bundle/bundle.sh --check` — refresh
   generated skill runtime + plugin copies of the viewer and SKILL.md.
5. `scripts/test/test-js.sh` — broad JS wrapper before handoff.
6. Manual smoke, from `skills/cad-viewer`:
   - free port: `npm --prefix scripts/viewer run agent:start -- --host
     127.0.0.1 --dir "<repo>/models"` → starts, URL printed, `?dir=` correct;
   - rerun same command → `action:"reuse"`, directory activated;
   - occupy 4178 with `python3 -m http.server 4178` → launcher exits 1 with
     the `--port` hint; rerun with `--port 4321` → starts there.

## Explicitly out of scope

- No `.claude/launch.json`.
- No changes to `packages/cadjs`, `packages/implicitjs`, docs site, or release
  tooling.
- `plugins/cad/VERSION` untouched (release workflow owns it).
