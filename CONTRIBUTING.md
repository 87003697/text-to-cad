# Contributing

This repository is a local workbench for CAD-related agent skills. Treat
`skills/` as the product under test and `models/` as the shared
fixture/artifact area.

## Local Checkout

Clone the repository:

```bash
git clone https://github.com/earthtojake/text-to-cad.git
cd text-to-cad
```

Create the repo-local Python development environment:

```bash
python3.11 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements-dev.txt
```

`requirements-dev.txt` installs the source packages from `packages/` and
`viewer/moveit2_server`, plus the small set of Python extras mirrored from
skill runtime requirements. This is the default Python environment for broad
repo checks and source-checkout development.

The CAD and cad-viewer requirements install their generated, skill-local
`cadpy` packages. URDF, SRDF, and SDF install their generated, skill-local
`cadpy_metadata` packages. `cadpy` owns the heavy CAD dependencies such as
`build123d`, `cadquery-ocp`, `numpy`, `trimesh`, and `vtk`; `ezdxf` and
`playwright` are CAD skill dependencies outside `cadpy`.

For CAD Viewer development:

```bash
npm --prefix viewer install
```

When running a tool manually, use that skill's interpreter:

```bash
.venv/skills/cad/bin/python skills/cad/scripts/step --help
.venv/skills/urdf/bin/python skills/urdf/scripts/urdf --help
```

After changing `packages/cadpy` or `packages/cadpy_metadata`, refresh the
generated copies with the relevant `scripts/build/build-*-skill.sh` command,
then reinstall the affected skill environment.

## Link Skills Into Your Agent

For local development, symlink this checkout's supported skill directories into
your agent. Do not copy skill directories into your agent: symlinks keep edits
in this checkout visible immediately.

Use the installer from the repository root:

```bash
scripts/dev/install-skills-dev.sh --agent codex
```

To see supported agents and resolved destination directories:

```bash
scripts/dev/install-skills-dev.sh --list-agents
```

The installer creates one symlink per supported skill. It leaves existing
non-symlink paths untouched.

Supported skill directories:

```text
bambu-labs
cad
cad-viewer
gcode
sdf
sendcutsend
srdf
step-parts
urdf
```

Supported local-development agent destinations:

| Agent flag  | Destination                                       |
| ----------- | ------------------------------------------------- |
| `codex`     | `${CODEX_HOME:-$HOME/.codex}/skills`              |
| `claude`    | `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills`      |
| `gemini`    | `$HOME/.gemini/skills`                            |
| `universal` | `${XDG_CONFIG_HOME:-$HOME/.config}/agents/skills` |
| `project`   | `.agents/skills` in this repository               |

`claude-code`, `gemini-cli`, `agents`, and `repo` are accepted aliases. Use
`--all` to install into every destination above, or repeat `--agent` for a
smaller set:

```bash
scripts/dev/install-skills-dev.sh --agent codex --agent claude
```

Restart or reload the agent after linking so it rescans available skills.

To remove this checkout's skill links while testing provider behavior:

```bash
scripts/dev/uninstall-skills-dev.sh --agent codex
```

The uninstaller removes only symlinks that point back at this checkout and
prunes empty destination directories unless `--keep-empty-dirs` is passed.

## Test From This Repository

Run development and test prompts from inside this repository instead of a
separate project checkout. The skills assume this workbench layout while you are
iterating: `models/` contains fixtures and generated CAD artifacts, `viewer/`
contains the editable CAD Viewer source, and repo-relative validation commands
live under `scripts/`.

Write test, sample, and durable CAD/robot-description artifacts under `models/`;
do not create ad hoc artifact directories elsewhere. When you need a scratch
project, create it under this checkout, for example:

```bash
mkdir -p models/experiments/my-test
```

Then start your agent with `/path/to/text-to-cad` as the working directory and
ask it to write files under that scratch path. This keeps skill scripts,
fixtures, generated sidecars, and Viewer links using the same repo-relative
paths that CI and local checks expect.

## Source Boundaries

Each skill must be self-contained and independent at runtime: it must not import
or depend on code from another skill or from the repository root.

Root source directories are canonical. `viewer/`, `packages/cadjs`, and
`packages/cadpy` are the source of truth for CAD Viewer and shared CAD runtime
behavior. Generated copies live under paths such as
`skills/cad-viewer/scripts/viewer`, `skills/cad-viewer/scripts/packages/`,
`skills/cad/scripts/packages/`, and snapshot runtimes. Do not patch those
generated copies as the lasting fix.

When changing skill behavior that uses `packages/cadjs`, `packages/cadpy`, or
the cad-viewer generated runtime, edit the root source in `packages/*` or
`viewer/*`, then rebuild the generated skill copies.

## Iteration Loop

1. Edit the relevant skill under `skills/<skill-name>/`.
2. Keep skill instructions narrow and executable: say when the skill applies,
   what inputs it expects, what it produces, and how to validate the work.
3. Prefer small files in `references/` and reusable scripts in `scripts/` over
   long inline instructions.
4. Add or update focused fixtures, tests, or benchmark cases when skill behavior
   changes so regressions are measurable.
5. Validate with the smallest relevant check before broad repo checks.

Generated artifacts should not become skill logic unless they are intentional
fixtures. Prefer source files plus deterministic regeneration.

## CAD And Viewer Checks

Use path-targeted validation. Common checks from the repo root:

```bash
scripts/test.sh
scripts/build.sh --check
scripts/build/build-cad-skill.sh --check
scripts/build/build-viewer.sh --check
scripts/build/build-cad-viewer-skill.sh --check
scripts/build/build-skills.sh --check
scripts/check/validate-plugins.sh
npm --prefix viewer run test
npm --prefix viewer run build
npm --prefix packages/cadjs test
npm --prefix docs run check
```

For targeted Python skill-script tests, run the relevant unittest files with the
repo-local Python runtime, for example:

```bash
./.venv/bin/python -m unittest skills/urdf/scripts/urdf/tests/test_cli.py
```

For fast CAD Viewer source iteration, run the root viewer app in dev mode. Do
not run the generated viewer from the cad-viewer skill while modifying Viewer
behavior:

```bash
npm --prefix viewer run dev -- --host 127.0.0.1
```

Use the printed URL with an absolute `?dir=/path/to/root` and any absolute
`?file=/path/to/model.step`. Do not assume a fixed dev port unless you pass
Vite's standard `--port` flag. For packaged cad-viewer skill runtime checks,
use `npm --prefix skills/cad-viewer/scripts/viewer run serve -- --host
127.0.0.1 --port 4178 --shutdown-after 12h` and include `?dir=` in every
returned handoff link. After changing Viewer source or shared render code,
rebuild the packaged cad-viewer skill runtime only when those changes need to be
reflected in the production skill runtime:

```bash
scripts/build/build-cad-viewer-skill.sh
scripts/build/build-cad-viewer-skill.sh --check
```

## Git Hygiene

Do not commit local environments, dependency folders, caches, or temp files such
as `.venv/`, `node_modules/`, `.vite/`, `dist/`, `tmp/`, or local credentials.
Generated runtime changes should come from the repo build scripts, not manual
edits inside generated runtime folders.

CAD exchange files, generated render/topology assets, `assets/**`, and
`benchmarks/**` may be LFS-tracked. Never disable LFS filters for `git add`,
commits, or other object-writing operations.
