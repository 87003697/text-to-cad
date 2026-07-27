#!/usr/bin/env python3
"""
rollout-usage.py — compute token usage + Venus GPT-5.6-sol cost from a
Codex rollout.jsonl.

Rollout schema (relevant subset)
--------------------------------
Codex writes one JSONL event per turn (and per lifecycle transition) into
its session rollout. We only care about one event kind:

    {"type": "event_msg",
     "payload": {"type": "token_count",
                 "info": {"total_token_usage": {
                     "input_tokens": 12700,
                     "cached_input_tokens": 0,
                     "output_tokens": 188,
                     ...
                 }}}}

``total_token_usage`` is **cumulative from session start**, so a rollout with
40 turns has 40 such events and each new event supersedes the previous. We
iterate the whole file and keep only the last one — that's the session total.

The rest of a rollout's events (session_meta, response_item, reasoning, etc.)
are ignored here; see mesh-to-cad's tooling for those.

Robustness: we tolerate malformed JSON lines (a rollout truncated by an OOM
kill can end mid-line) and missing fields — anything unexpected just gets
skipped. If no token_count event is found we exit with a diagnostic; that
means the pilot never completed a turn.

Pricing (Venus GPT-5.6-sol, effective 2026-07)
----------------------------------------------
Uncached input   $5    per 1M tokens
Cached input     $0.5  per 1M tokens   (10x cheaper — worth it if prompt reused)
Output           $30   per 1M tokens   (typical LLM markup)

Update these constants when Venus changes pricing. Because this file is the
single source of truth for cost calculation (pilot-exec.sh does NOT precompute
a usage.json), changing the number here re-prices all historical rollouts.

Usage
-----
    rollout-usage.py ROLLOUT.jsonl [--label LABEL]

Default label = basename of the rollout's parent directory (which equals the
experiment dir when rollout.jsonl sits inside its EXP_DIR). Override via
``--label`` when the rollout lives elsewhere (e.g. under ~/.codex/sessions/).
"""

import argparse
import json
import pathlib
import sys


# Venus GPT-5.6-sol pricing, per token (convert from the $-per-1M-tokens
# figures in the docstring). Kept as module constants so a pricing update
# is a one-line diff visible to git blame.
PRICE_INPUT_PER_TOKEN = 5e-6      # $5 / 1e6 tokens
PRICE_CACHED_PER_TOKEN = 0.5e-6   # $0.5 / 1e6 tokens
PRICE_OUTPUT_PER_TOKEN = 30e-6    # $30 / 1e6 tokens


def extract_usage(rollout_path: pathlib.Path) -> dict | None:
    """Return the last token_count event's ``total_token_usage`` dict, or None.

    Iterates every line, silently skips non-JSON / non-event_msg / non-
    token_count lines. Keeps overwriting ``usage`` so the final value is the
    latest cumulative total (which is what we want — the session total).
    """
    usage = None
    for line in rollout_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            # Truncated / corrupted lines are tolerated: a rollout can end
            # mid-line if the process was killed. Skip and move on.
            continue
        if evt.get("type") != "event_msg":
            continue
        payload = evt.get("payload") or {}
        if payload.get("type") != "token_count":
            continue
        tt = (payload.get("info") or {}).get("total_token_usage")
        if tt:
            usage = tt  # overwrite; last one wins (= cumulative session total)
    return usage


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compute token/cost summary from a Codex rollout.jsonl"
    )
    parser.add_argument("rollout", type=pathlib.Path, help="path to rollout.jsonl")
    parser.add_argument(
        "--label",
        help="label field in the emitted JSON (default: rollout's parent dir name)",
    )
    args = parser.parse_args()

    if not args.rollout.is_file():
        print(f"rollout not found: {args.rollout}", file=sys.stderr)
        return 1

    usage = extract_usage(args.rollout)
    if usage is None:
        # No token_count event means the pilot exited before finishing even
        # one turn — either codex crashed at startup or the sandbox blew up.
        # The rollout might still contain a session_meta line; investigate.
        print(f"no token_count event in {args.rollout}", file=sys.stderr)
        return 2

    inp = usage["input_tokens"]
    out = usage["output_tokens"]
    cached = usage["cached_input_tokens"]
    # Uncached portion of input is billed at the higher rate; the rest is at
    # the cached rate. Output is always at the output rate.
    #   cost = (input - cached) * P_input + cached * P_cached + output * P_output
    # ``max(inp - cached, 0)`` guards against future Venus returning cached
    # > input in some corner case (shouldn't happen but cheap to be safe).
    cost = (
        max(inp - cached, 0) * PRICE_INPUT_PER_TOKEN
        + cached * PRICE_CACHED_PER_TOKEN
        + out * PRICE_OUTPUT_PER_TOKEN
    )

    summary = {
        "label": args.label or args.rollout.parent.name,
        "input_tokens": inp,
        "cached_input_tokens": cached,
        "output_tokens": out,
        "estimated_cost_usd": round(cost, 4),
    }
    json.dump(summary, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
