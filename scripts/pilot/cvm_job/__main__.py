from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .protocol import ProtocolError, default_state_root
from .runtime import (
    DEFAULT_STALE_AFTER,
    DEFAULT_WAIT_TIMEOUT,
    diagnose_job,
    status_job,
    submit_provider_free_installed_plugin,
    submit_pilot,
    supervise_provider_free_installed_plugin,
    supervise_pilot,
    wait_job,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m scripts.pilot.cvm_job")
    parser.add_argument("--state-root", type=Path, default=None, help=argparse.SUPPRESS)
    subparsers = parser.add_subparsers(dest="command", required=True)

    pilot = subparsers.add_parser("submit-pilot")
    pilot.add_argument("object")
    pilot.add_argument("group")

    provider_free = subparsers.add_parser("submit-provider-free")
    provider_free.add_argument("scenario", choices=("installed-plugin",))
    provider_free.add_argument("group")

    supervise_one = subparsers.add_parser("supervise-pilot")
    supervise_one.add_argument("--job", required=True)

    supervise_provider_free = subparsers.add_parser("supervise-provider-free")
    supervise_provider_free.add_argument("--job", required=True)

    status = subparsers.add_parser("status")
    status.add_argument("handle")
    status.add_argument("--stale-after", type=float, default=DEFAULT_STALE_AFTER)

    diagnose = subparsers.add_parser("diagnose")
    diagnose.add_argument("handle")

    wait = subparsers.add_parser("wait")
    wait.add_argument("handle")
    wait.add_argument(
        "--until",
        choices=("terminal", "terminal-or-stale"),
        default="terminal",
    )
    wait.add_argument("--timeout", type=float, default=DEFAULT_WAIT_TIMEOUT)
    wait.add_argument("--stale-after", type=float, default=DEFAULT_STALE_AFTER)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.state_root or default_state_root()
    try:
        if args.command == "submit-pilot":
            result = submit_pilot(args.object, args.group, state_root=root)
            status = 0 if result["state"] != "failed" else 1
        elif args.command == "submit-provider-free":
            result = submit_provider_free_installed_plugin(
                args.scenario, args.group, state_root=root
            )
            status = 0 if result["state"] != "failed" else 1
        elif args.command == "supervise-pilot":
            result = supervise_pilot(args.job, state_root=root)
            status = 0 if result["state"] == "succeeded" else 1
        elif args.command == "supervise-provider-free":
            result = supervise_provider_free_installed_plugin(
                args.job, state_root=root
            )
            status = 0 if result["state"] == "succeeded" else 1
        elif args.command == "status":
            result = status_job(
                args.handle,
                state_root=root,
                stale_after=args.stale_after,
            )
            status = 0
        elif args.command == "diagnose":
            result = diagnose_job(
                args.handle,
                state_root=root,
            )
            status = 1
        else:
            result, status = wait_job(
                args.handle,
                state_root=root,
                until=args.until,
                timeout=args.timeout,
                stale_after=args.stale_after,
            )
    except ProtocolError as error:
        print(json.dumps({"ok": False, "error": str(error)}, separators=(",", ":")))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
