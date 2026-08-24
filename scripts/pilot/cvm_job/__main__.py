from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .model import MODEL_SELECTORS
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


class _UniqueReconstructionSpecAction(argparse.Action):
    """Set the Reconstruction Spec mode while rejecting repeated flags."""

    _SEEN_ATTRIBUTE = "_reconstruction_spec_flags_seen"

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        if getattr(namespace, self._SEEN_ATTRIBUTE, False):
            parser.error("reconstruction spec flags may not be repeated")
        setattr(namespace, self._SEEN_ATTRIBUTE, True)
        setattr(namespace, self.dest, self.const)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m scripts.pilot.cvm_job")
    parser.add_argument("--state-root", type=Path, default=None, help=argparse.SUPPRESS)
    subparsers = parser.add_subparsers(dest="command", required=True)

    pilot = subparsers.add_parser("submit-pilot")
    pilot.add_argument("object")
    pilot.add_argument("group")
    pilot.add_argument(
        "--model",
        choices=MODEL_SELECTORS,
        default=None,
        help="model selector (default: gpt-5.5)",
    )
    pilot.add_argument("--plugin-mode", choices=("direct", "e2e"), default="direct")
    reconstruction = pilot.add_mutually_exclusive_group()
    reconstruction.add_argument(
        "--reconstruction-spec",
        dest="reconstruction_spec",
        action=_UniqueReconstructionSpecAction,
        const=True,
        nargs=0,
        default=True,
        help="enable the Reconstruction Spec workflow (default)",
    )
    reconstruction.add_argument(
        "--no-reconstruction-spec",
        dest="reconstruction_spec",
        action=_UniqueReconstructionSpecAction,
        const=False,
        nargs=0,
        default=True,
        help="disable the Reconstruction Spec workflow",
    )

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
            result = submit_pilot(
                args.object,
                args.group,
                model=args.model,
                plugin_mode=args.plugin_mode,
                reconstruction_spec=args.reconstruction_spec,
                state_root=root,
            )
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
