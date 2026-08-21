# AGENTS.md

CAD-related agent skills workbench. `skills/` is the product; `models/` holds
shared fixtures/artifacts; `packages/` holds shared runtime code that skills
vendor from.

This file is for **development agents** (working on the repo). Execution
agents (codex running a pilot) read `skills/<name>/SKILL.md` at runtime and
don't need this file.

## Landmines — do not step on these

- **Branches.** Work on `develop`. Do not use `main` for dev work.
- **CVM operations.** Push code, pull pilot outputs, batch code snapshots, and
  Browser Runtime image provisioning/probes go through skills — `/cvm-push`,
  `/cvm-pull`, `/snapshot-batch`, `/cvm-browser-runtime` — not raw
  `rsync`/`scp`/`aws s3 cp`. See
  `.claude/skills/cvm-*/SKILL.md`.
- **Skill isolation.** Skills are self-contained at runtime. No imports
  between skills or from `skills/` root. Shared helpers live in
  `packages/` and are vendored/generated into consuming skill runtimes.
- **LFS.** Never disable LFS filters for `git add`, commits, or
  object-writing operations.
- **Plugin root.** The repository root is the plugin package:
  `.claude-plugin/` and `.codex-plugin/` hold its manifests, and
  `skills/` is the canonical plugin skill tree. Do not recreate
  `plugins/cad/` or a separate plugin bundle/copy step. Codex plugin
  installation requires Codex 0.142.0 or newer.
- **Symlinks.** `develop` uses symlinks across generated runtime and
  viewer package paths. Edit the symlink target/source, not the copy.
  Production trees must contain no symlinks because provider installers
  handle them inconsistently and Codex can silently omit them. Set up
  the development layout via `scripts/dev/setup-symlinks.sh`.

## Where things live

**Code**
- `skills/`: agent skills + `references/`, `scripts/`
- `.claude-plugin/`, `.codex-plugin/`: provider manifests for the
  repo-root plugin package
- `VERSION`: canonical repository/plugin release version
- `plugins/`: versioned plugin packages bundling repo skills
- `packages/cadjs`, `packages/cadgen`, `packages/agent_runtime`,
  `packages/browser_runtime`, `packages/meshscope`, and `packages/meshshot`:
  shared runtime code (framework-agnostic, siblings
  do not import each other)
- `viewer/`: editable CAD Viewer source app
- `docs/`: documentation site
- `scripts/`: durable repo commands (bundle, test, install, release,
  dev, viewer, utils)
- `tests/`: root-owned test suites

**Fixtures & outputs**
- `models/`: sample and durable CAD/robot-description fixtures. Write
  ALL test / permanent / generated CAD artifacts here (STEP, STL, GLB,
  DXF, URDF, SRDF, SDF, G-code). No ad hoc artifact directories.
- `outputs/`: local pilot outputs (git-ignored; `<group>/<exp>/` layout)
- `tmp/` or `/tmp/`: one-off / local-only helpers (do not put temporary
  scripts under `scripts/`)

## Where to read

| For | Read |
|-----|------|
| CVM environment (SSH, dirs, ports, secrets, quotas) | `.agents/DEVCLOUD.md` |
| Anything else the dev agent may need (accumulated notes, architecture decisions, current + past plans, session handoffs) | `.agents/` |

## Test / build

Run the smallest path-targeted check that covers the change:

- Code tests: `scripts/test/test.sh` (or focused runners `test-js.sh`,
  `test-docs.sh`, `test-python.sh`, `test-global.sh`)
- Symlink layout: `scripts/dev/setup-symlinks.sh --check`
- Generated runtime freshness: `scripts/bundle/bundle.sh --check` (run
  `scripts/bundle/bundle.sh` first if source changes affect generated
  runtimes)
- Viewer / packages: `npm --prefix <path> test|build`
- Docs: `npm --prefix docs run check`
- Targeted Python: `./.venv/bin/python -m unittest <changed paths>`

## Environments (quick notes)

- Prefer `./.venv/bin/python` for CAD Python work.
- Keep new worktrees lightweight — do not copy `.venv/` or `models/`;
  recreate `.venv/` and hydrate `models/` (via `git lfs checkout <path>`)
  only when the workflow needs them.
- Install dependencies only for the workflow being changed.
- Do not commit `.venv/`, `node_modules/`, caches, `tmp/`, credentials,
  or printer config.
