#!/usr/bin/env bash
# CAD Viewer DEV preview for Claude Code's .claude/launch.json.
#
# Repo developer-workflow tooling ONLY — deliberately NOT part of the cad-viewer
# skill. Runs the Vite dev server (client HMR + it spawns the Python backend) so
# you iterate against source. For a production build use `npm run start` instead
# (see AGENTS.md); the skill's `start` command serves the built bundle.
#
# Claude Code's autoPort assigns a free port per worktree/branch and exports it
# as $PORT; this forwards it to Vite, so iterating in multiple worktrees never
# collides. When $PORT is unset (3245 was free), Vite uses its default.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"

export VIEWER_DEFAULT_DIR="${repo_root}/models"
exec npm --prefix "${repo_root}/viewer" run dev -- --host 127.0.0.1 ${PORT:+--port "${PORT}"}
