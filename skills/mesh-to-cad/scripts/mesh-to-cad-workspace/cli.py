"""Public command-line interface for immutable Mesh-to-CAD Workspaces."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from workspace_core import (
    DEFAULT_COMMAND_SECONDS,
    FAILED_ATTEMPT_RESULTS,
    WorkspaceError,
    begin_attempt,
    finalize_workspace,
    initialize_workspace,
    publish_step_zero,
    publish_cycle,
    record_attempt,
    recover_workspace,
    rebuild_index,
    run_attempt_command,
    validate_workspace,
    workspace_status,
)


class _HelpRequested(Exception):
    pass


class _JsonArgumentParser(argparse.ArgumentParser):
    """Convert command-line contract errors into the public JSON error shape."""

    def error(self, message: str) -> None:
        raise WorkspaceError("invalid_arguments", message, "$.argv")

    def print_help(self, file=None) -> None:
        target = file if file is not None else sys.stdout
        print(
            json.dumps(
                {
                    "ok": True,
                    "help": {"program": self.prog, "text": self.format_help()},
                },
                separators=(",", ":"),
            ),
            file=target,
        )

    def exit(self, status: int = 0, message: str | None = None) -> None:
        if status == 0 and message is None:
            raise _HelpRequested
        super().exit(status, message)


def _emit(value: dict) -> None:
    print(json.dumps(value, separators=(",", ":")))


def _workspace_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace", type=Path, required=True)


def _parser() -> argparse.ArgumentParser:
    parser = _JsonArgumentParser(prog="mesh-to-cad-workspace")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="Publish prepared canonical setup")
    _workspace_argument(init)
    init.add_argument("--prepared", type=Path, required=True)

    begin = commands.add_parser("begin-attempt", help="Freeze one bounded attempt")
    _workspace_argument(begin)
    begin.add_argument("--plan", type=Path, required=True)
    begin.add_argument("--intended-step", type=int, required=True)
    begin.add_argument("--from-step", type=int)

    step_zero = commands.add_parser(
        "publish-step-zero", help="Publish the initial Measured Step"
    )
    _workspace_argument(step_zero)
    step_zero.add_argument("--attempt", type=int, required=True)
    step_zero.add_argument("--candidate", type=Path, required=True)
    step_zero.add_argument("--candidate-mesh", required=True)
    step_zero.add_argument("--measurement", type=Path, required=True)
    step_zero.add_argument("--preview", type=Path, required=True)

    run = commands.add_parser("run", help="Run one bounded argv command")
    _workspace_argument(run)
    run.add_argument("--attempt", type=int, required=True)
    run.add_argument("--phase", required=True)
    run.add_argument(
        "--timeout-seconds", type=int, default=DEFAULT_COMMAND_SECONDS
    )
    run.add_argument("argv", nargs=argparse.REMAINDER)

    record = commands.add_parser("record-attempt", help="Publish a failed Attempt")
    _workspace_argument(record)
    record.add_argument("--attempt", type=int, required=True)
    record.add_argument(
        "--result",
        choices=FAILED_ATTEMPT_RESULTS,
        required=True,
    )
    record.add_argument("--classification", required=True)

    cycle = commands.add_parser("publish-cycle", help="Publish one Repair Cycle")
    _workspace_argument(cycle)
    cycle.add_argument("--attempt", type=int, required=True)
    cycle.add_argument("--candidate", type=Path, required=True)
    cycle.add_argument("--candidate-mesh", required=True)
    cycle.add_argument("--measurement", type=Path, required=True)
    cycle.add_argument("--preview", type=Path, required=True)
    cycle.add_argument("--region-diff", type=Path, required=True)
    cycle.add_argument("--assessment", type=Path, required=True)
    cycle.add_argument("--source-changes", type=Path, required=True)

    finalize = commands.add_parser(
        "finalize", help="Rebuild, verify, and publish Final Delivery"
    )
    _workspace_argument(finalize)
    finalize.add_argument("--selection", type=Path, required=True)
    finalize.add_argument("--notes", type=Path, required=True)
    finalize.add_argument("--rebuild-entrypoint", type=Path, required=True)
    finalize.add_argument("--geometry-entrypoint", type=Path, required=True)
    finalize.add_argument("--tool-registry", type=Path, required=True)

    validate = commands.add_parser("validate", help="Validate Workspace authority")
    _workspace_argument(validate)

    rebuild = commands.add_parser("rebuild-index", help="Rebuild the derived graph index")
    _workspace_argument(rebuild)

    status = commands.add_parser("status", help="Show compact objective state")
    _workspace_argument(status)

    recover = commands.add_parser("recover", help="Recover validated transactions")
    _workspace_argument(recover)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        if args.command == "init":
            value = initialize_workspace(args.workspace, args.prepared)
            _emit({"ok": True, "workspace": value})
        elif args.command == "begin-attempt":
            value = begin_attempt(
                args.workspace,
                args.plan,
                intended_step=args.intended_step,
                from_step=args.from_step,
            )
            _emit({"ok": True, "attempt": value})
        elif args.command == "publish-step-zero":
            value = publish_step_zero(
                args.workspace,
                attempt=args.attempt,
                candidate=args.candidate,
                candidate_mesh=args.candidate_mesh,
                measurement=args.measurement,
                preview=args.preview,
            )
            _emit({"ok": True, "step": value})
        elif args.command == "run":
            argv = list(args.argv)
            if argv[:1] == ["--"]:
                argv = argv[1:]
            value = run_attempt_command(
                args.workspace,
                attempt=args.attempt,
                phase=args.phase,
                argv=argv,
                timeout_seconds=args.timeout_seconds,
            )
            _emit({"ok": value["exit_code"] == 0, "command": value})
            return value["exit_code"]
        elif args.command == "record-attempt":
            value = record_attempt(
                args.workspace,
                attempt=args.attempt,
                result=args.result,
                classification=args.classification,
            )
            _emit({"ok": True, "attempt": value})
        elif args.command == "publish-cycle":
            value = publish_cycle(
                args.workspace,
                attempt=args.attempt,
                candidate=args.candidate,
                candidate_mesh=args.candidate_mesh,
                measurement=args.measurement,
                preview=args.preview,
                region_diff=args.region_diff,
                assessment=args.assessment,
                source_changes=args.source_changes,
            )
            _emit({"ok": True, "cycle": value})
        elif args.command == "finalize":
            value = finalize_workspace(
                args.workspace,
                selection=args.selection,
                notes=args.notes,
                rebuild_entrypoint=args.rebuild_entrypoint,
                geometry_entrypoint=args.geometry_entrypoint,
                tool_registry=args.tool_registry,
            )
            _emit({"ok": True, "final": value})
        elif args.command == "validate":
            result = validate_workspace(args.workspace)
            _emit({"ok": True, "valid": True, "graph": result.graph, "recovery": result.recovery})
        elif args.command == "rebuild-index":
            _emit({"ok": True, "step_index": rebuild_index(args.workspace)})
        elif args.command == "status":
            _emit({"ok": True, "status": workspace_status(args.workspace)})
        elif args.command == "recover":
            _emit({"ok": True, "recovery": recover_workspace(args.workspace)})
        else:  # pragma: no cover
            raise AssertionError(args.command)
    except WorkspaceError as exc:
        _emit(
            {
                "ok": False,
                "error": {
                    "classification": exc.classification,
                    "path": exc.path,
                    "detail": exc.detail,
                },
            }
        )
        print(f"{exc.classification}: {exc.path}: {exc.detail}", file=sys.stderr)
        return 2
    except _HelpRequested:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
