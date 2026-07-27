#!/usr/bin/env bash
#
# sandbox-init.sh EXP_DIR — set up a per-pilot bwrap-isolated ~/.codex
# overlay and print shell exports the caller can eval to run commands
# inside the sandbox.
#
# Layer role
# ----------
# This is the generic "how do we run something in an isolated view of
# ~/.codex" layer. It doesn't know what runs inside (codex, bash, whatever)
# — it only sets up the overlay and hands back a bwrap command prefix.
# Codex-specific things (git init, .gitignore, gateway wrapping) live in
# codex-init.sh which layers on top of this.
#
# See sandbox-clean.sh for tear-down.
#
# Usage
# -----
#   eval "$(sandbox-init.sh EXP_DIR)"
#   $SANDBOX_RUN <any command>
#
# What gets exported (via eval'd stdout)
# --------------------------------------
#   SANDBOX_UPPER   Absolute path to the overlay's writable "upper" layer.
#                   All writes made inside the sandbox to what looks like
#                   ~/.codex land here (rollout, state sqlite, plugin
#                   locks, etc.). Callers extract artifacts from this path.
#
#   SANDBOX_RUN     A bwrap command prefix ready to prepend to any command,
#                   ending with `--` so everything after is the command to
#                   run inside the sandbox. Example:
#                       $SANDBOX_RUN gateway/codex-gpt56 sol exec ...
#
# Caveat: because SANDBOX_RUN is a shell string that bash tokenizes on
# whitespace at use time, paths embedded in it must not contain spaces
# or shell metacharacters. EXP_DIR paths in this project follow
# outputs/<group>/<ts>-<slug>/ which is space-free.
#
# Reference bwrap capabilities
# ----------------------------
# SANDBOX_RUN carries a fixed set of bwrap flags. Two groups:
#
#   Structural (never change — sandbox breaks without them):
#     --dev-bind / /              Host root visible RW except where overlay
#                                 shadows. Sandboxed process reads /usr/bin,
#                                 /lib, /etc and writes /tmp and workspace
#                                 dirs.
#     --proc /proc                Fresh /proc mount. Some tools inspect
#                                 /proc and need a real mount.
#     --overlay-src $HOME/.codex  Overlay lower (read-only) layer — the
#                                 real ~/.codex, seen unchanged from inside
#                                 the sandbox until something writes.
#     --overlay UPPER WORK dst    Overlay upper (writable) layer + kernel
#                                 scratch. Writes to dst land in UPPER.
#     --die-with-parent           Sandbox dies when the caller dies. Cheap
#                                 orphan prevention.
#
#   Scenario-specific (reflect "codex-on-Venus" use case):
#     --share-net                 Keep host network namespace. Codex needs
#                                 HTTPS to Venus.
#     --setenv VENUS_TOKEN <val>  Propagate the caller's token so codex can
#                                 authenticate. Value baked into SANDBOX_RUN.
#
# There is intentionally NO CLI knob to override these — exposing knobs
# would push bwrap knowledge back to the caller, which is exactly what
# this script exists to hide. Edit this file if you need a variant.
#
# Environment
# -----------
#   VENUS_TOKEN   required — Venus API bearer token. Embedded into
#                 SANDBOX_RUN via --setenv.
#
# Exit codes
# ----------
#   0    success (exports printed to stdout)
#   1    preflight failure (missing VENUS_TOKEN or bwrap)
#   2    EXP_DIR does not exist

set -euo pipefail

EXP_DIR="${1:?Usage: sandbox-init.sh EXP_DIR}"

# Preflight
: "${VENUS_TOKEN:?VENUS_TOKEN must be set (source ~/.secrets/text-to-cad.env)}"
command -v bwrap >/dev/null \
    || { echo "bwrap not installed; run: dnf install -y bubblewrap" >&2; exit 1; }
[[ -d "$EXP_DIR" ]] || { echo "EXP_DIR not found: $EXP_DIR" >&2; exit 2; }

# Resolve to absolute paths — bwrap requires absolute paths for
# overlay-src / overlay mount points.
EXP_DIR_ABS="$(cd "$EXP_DIR" && pwd)"
UPPER="$EXP_DIR_ABS/.codex-upper"
WORK="$EXP_DIR_ABS/.codex-work"
mkdir -p "$UPPER" "$WORK"

# Print exports for the caller to `eval`.
cat <<EOF
export SANDBOX_UPPER="$UPPER"
export SANDBOX_RUN="bwrap --dev-bind / / --proc /proc --overlay-src $HOME/.codex --overlay $UPPER $WORK $HOME/.codex --share-net --die-with-parent --setenv VENUS_TOKEN $VENUS_TOKEN --"
EOF
