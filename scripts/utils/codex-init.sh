#!/usr/bin/env bash
#
# codex-init.sh EXP_DIR — prepare an experiment directory for a codex pilot.
#
# Layer role
# ----------
# Sits on top of sandbox-init.sh (the generic bwrap layer) and adds all
# codex-specific setup:
#   * git init inside EXP_DIR (agent commits per phase during the pilot)
#   * .gitignore filtering codex artifacts
#   * A composed CODEX_RUN command prefix so callers say
#         $CODEX_RUN "$PROMPT"
#     instead of manually chaining bwrap + gateway + exec + flags.
#
# See codex-exit.sh for the paired teardown.
#
# Usage
# -----
#   eval "$(codex-init.sh EXP_DIR)"
#   $CODEX_RUN "$PROMPT" < /dev/null > /dev/null 2> "$EXP_DIR/stderr.log"
#
# What gets exported (via eval'd stdout)
# --------------------------------------
#   SANDBOX_UPPER   passthrough from sandbox-init.sh
#   SANDBOX_RUN     passthrough from sandbox-init.sh
#   CODEX_RUN       "$SANDBOX_RUN <REPO_ROOT>/gateway/codex-gpt56 <MODEL> exec -s workspace-write"
#                   — a full "run codex inside the sandbox" prefix, ready
#                     to be followed by the prompt string.
#
# Environment
# -----------
#   VENUS_TOKEN   required (see sandbox-init.sh)
#   MODEL         optional — Venus GPT-5.6 variant (sol/terra/luna,
#                 default sol). Baked into CODEX_RUN.
#
# Exit codes
# ----------
#   0    success (exports printed to stdout)
#   1    preflight failure inherited from sandbox-init.sh
#   2    EXP_DIR could not be created

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

EXP_DIR="${1:?Usage: codex-init.sh EXP_DIR}"

# 1. Ensure EXP_DIR exists (sandbox-init.sh requires this).
mkdir -p "$EXP_DIR"

# 2. Git repo + .gitignore. Agent uses per-phase commits per
#    skills/mesh-to-cad/references/output-schemas.md § Git commit conventions.
#    Idempotent: `git init` on an existing repo is a no-op, and the
#    initial commit is only made if HEAD doesn't already exist.
(
    cd "$EXP_DIR"
    [[ -d .git ]] || git init --quiet
    cat > .gitignore <<'GITIGNORE'
stderr.log
rollout.jsonl
prompt.txt
.codex-upper/
.codex-work/
__pycache__/
*.pyc
.codex/
GITIGNORE
    if ! git rev-parse --verify HEAD >/dev/null 2>&1; then
        git add .gitignore
        git -c user.name=pilot -c user.email=pilot@localhost \
            commit --quiet -m "pilot: initial commit"
    fi
)

# 3. Delegate the bwrap overlay setup. We capture the exports script,
#    eval it locally so we have SANDBOX_RUN in scope for composing
#    CODEX_RUN, and then re-emit it (plus CODEX_RUN) for our own caller.
sandbox_exports="$("$SCRIPT_DIR/sandbox-init.sh" "$EXP_DIR")"
eval "$sandbox_exports"

# 4. Compose CODEX_RUN = sandbox prefix + gateway/codex-gpt56 exec prefix.
#    The caller does `$CODEX_RUN "$PROMPT"` — one string to invoke codex
#    inside the sandbox.
CODEX_RUN="$SANDBOX_RUN $REPO_ROOT/gateway/codex-gpt56 ${MODEL:-sol} exec -s workspace-write"

# 5. Print all exports for the caller to eval.
echo "$sandbox_exports"
echo "export CODEX_RUN=\"$CODEX_RUN\""
