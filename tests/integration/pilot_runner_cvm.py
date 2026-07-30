#!/usr/bin/env python3
"""Exercise the pilot runner against real Linux bwrap without Venus traffic."""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER = REPO_ROOT / "scripts" / "pilot" / "runner.py"
GATEWAY = REPO_ROOT / "gateway" / "codex-tap-gpt56"
READY_PATTERN = re.compile(r"listening on http://127\.0\.0\.1:(\d+)")


class CheckFailure(RuntimeError):
    """One real-CVM contract check failed."""


@dataclass(frozen=True)
class Case:
    """Paths and argv for one isolated fake workload."""

    exp_dir: Path
    argv: list[str]


def require(condition: bool, message: str) -> None:
    """Raise a concise integration failure when condition is false."""

    if not condition:
        raise CheckFailure(message)


def process_ids(name: str) -> set[int]:
    """Return exact-name process ids without failing when none exist."""

    result = subprocess.run(
        ["pgrep", "-x", name],
        check=False,
        capture_output=True,
        text=True,
    )
    return {
        int(line)
        for line in result.stdout.splitlines()
        if line.strip().isdigit()
    }


def runner_env(**overrides: str) -> dict[str, str]:
    """Return a deterministic host environment with a non-secret dummy token."""

    environ = dict(os.environ)
    environ.update(
        {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "TAP_READY_TIMEOUT": "10",
            "TAP_STOP_TIMEOUT": "10",
            "VENUS_TOKEN": "cvm-half-integration-dummy",
            "UNRELATED_SECRET": "must-not-cross-sandbox",
            "CODEX_BIN": "/must/not/cross",
        }
    )
    environ.update(overrides)
    return environ


def choose_inputs() -> tuple[Path, Path | None]:
    """Choose one declared model and, when available, one hidden sibling."""

    inputs = sorted((REPO_ROOT / "models" / "toys4k").glob("*.ply"))
    require(bool(inputs), "CVM models/toys4k has no hydrated PLY input")
    return inputs[0], inputs[1] if len(inputs) > 1 else None


def fake_workload_text(
    exp_relative: Path,
    input_relative: Path,
    other_input_relative: Path | None,
    sibling_exp_relative: Path,
    *,
    sleep_seconds: int = 0,
) -> str:
    """Build a sandbox-side assertion script that emits a synthetic rollout."""

    exp = f"/workspace/repo/{exp_relative.as_posix()}"
    declared_input = f"/workspace/repo/{input_relative.as_posix()}"
    other_input = (
        f"/workspace/repo/{other_input_relative.as_posix()}"
        if other_input_relative is not None
        else "/workspace/repo/models/toys4k/not-present.ply"
    )
    sibling_exp = f"/workspace/repo/{sibling_exp_relative.as_posix()}"
    return f"""#!/usr/bin/env bash
set -u
EXP={exp!r}
DECLARED_INPUT={declared_input!r}
OTHER_INPUT={other_input!r}
SIBLING_EXP={sibling_exp!r}
failures="$EXP/failures.txt"
: > "$failures"
fail() {{ printf '%s\n' "$1" >> "$failures"; }}

[[ "$(pwd)" == /workspace/repo ]] || fail cwd
[[ "$1" == "argument with spaces" ]] || fail argv_spaces
[[ -z "$2" ]] || fail argv_empty
[[ "$HOME" == /home/pilot ]] || fail home
[[ "$CODEX_HOME" == /home/pilot/.codex ]] || fail codex_home
[[ "$CLAUDE_TAP_URL" =~ ^http://127[.]0[.]0[.]1:[0-9]+/v1$ ]] || fail tap_url
[[ -z "${{UNRELATED_SECRET+x}}" ]] || fail unrelated_env
[[ -z "${{CODEX_BIN+x}}" ]] || fail codex_bin_env
[[ "$(command -v codex)" == /usr/bin/codex ]] || fail codex_path
[[ -r "$DECLARED_INPUT" ]] || fail input_missing
[[ ! -e "$OTHER_INPUT" ]] || fail other_input_visible
[[ ! -e "$SIBLING_EXP" ]] || fail sibling_exp_visible
[[ ! -e /workspace/repo/.git ]] || fail git_visible
[[ ! -e /workspace/repo/.agents ]] || fail agents_visible
[[ ! -e /workspace/repo/AGENTS.md ]] || fail repo_source_visible
[[ ! -e /root/.secrets ]] || fail host_secrets_visible
[[ ! -e /home/pilot/.codex/config.toml ]] || fail host_codex_config_visible
[[ -x /workspace/repo/gateway/codex-tap-gpt56 ]] || fail gateway_missing
[[ -x /workspace/repo/.venv/bin/python ]] || fail venv_missing
[[ -d /home/pilot/.cache/ms-playwright ]] || fail playwright_missing
[[ -f /workspace/repo/skills/cad/SKILL.md ]] || fail skill_missing
if ! /workspace/repo/gateway/codex-tap-gpt56 sol --version > "$EXP/codex-version.txt" 2> "$EXP/codex-version.stderr"; then
  fail codex_node_start
fi
grep -q '^codex-cli ' "$EXP/codex-version.txt" || fail codex_version

host_pid="$(cat "$EXP/host-pid.txt")"
if [[ -r "/proc/$host_pid/environ" ]] && tr '\0' '\n' < "/proc/$host_pid/environ" | grep -q '^HOST_PROCESS_SECRET='; then
  fail host_process_environ_visible
fi
cap_eff="$(awk '/^CapEff:/ {{print $2}}' /proc/self/status)"
[[ "$cap_eff" == 0000000000000000 ]] || fail capabilities
printf '%s\n' "$cap_eff" > "$EXP/cap-eff.txt"
find /proc -maxdepth 1 -type d -name '[0-9]*' -printf '%f\n' | sort -n > "$EXP/sandbox-pids.txt"
env | cut -d= -f1 | sort > "$EXP/child-env-keys.txt"

for target in \
  "$(dirname "$DECLARED_INPUT")/.pilot-write-test" \
  /workspace/repo/skills/cad/.pilot-write-test \
  /workspace/repo/gateway/.pilot-write-test \
  /workspace/repo/.venv/.pilot-write-test; do
  if touch "$target" 2>/dev/null; then
    rm -f "$target"
    fail "readonly:$target"
  fi
done

printf 'host-visible\n' > "$EXP/exp-write.txt" || fail exp_write
printf 'ephemeral\n' > /tmp/pilot-ephemeral.txt || fail tmp_write
mkdir -p "$EXP/reviews"
printf 'synthetic png\n' > "$EXP/reviews/final.png"
mkdir -p "$CODEX_HOME/sessions/2026/07/30"
printf '%s\n' '{{"timestamp":"2026-07-30T00:00:00Z","type":"session_meta","payload":{{"id":"cvm-half-integration","cwd":"/workspace/repo"}}}}' > "$CODEX_HOME/sessions/2026/07/30/rollout-cvm-half.jsonl"
printf 'started\n' > "$EXP/workload-started.txt"
trap 'printf "signal\n" > "$EXP/signal-received.txt"; exit 130' INT TERM
if [[ {sleep_seconds} -gt 0 ]]; then sleep {sleep_seconds}; fi
[[ ! -s "$failures" ]] || exit 22
exit 0
"""


def prepare_case(
    group_dir: Path,
    name: str,
    declared_input: Path,
    other_input: Path | None,
    sleeper_pid: int,
    *,
    sleep_seconds: int = 0,
) -> Case:
    """Create one host EXP and its sandbox-visible fake workload script."""

    exp_dir = group_dir / name
    sibling_exp = group_dir / "sibling-do-not-expose"
    sibling_exp.mkdir(parents=True, exist_ok=True)
    (sibling_exp / "marker.txt").write_text("hidden\n", encoding="utf-8")
    exp_dir.mkdir(parents=True, exist_ok=True)
    (exp_dir / "host-pid.txt").write_text(f"{sleeper_pid}\n", encoding="utf-8")
    script = exp_dir / "fake workload.sh"
    script.write_text(
        fake_workload_text(
            exp_dir.relative_to(REPO_ROOT),
            declared_input.relative_to(REPO_ROOT),
            other_input.relative_to(REPO_ROOT) if other_input else None,
            sibling_exp.relative_to(REPO_ROOT),
            sleep_seconds=sleep_seconds,
        ),
        encoding="utf-8",
    )
    script.chmod(0o755)
    sandbox_script = f"/workspace/repo/{script.relative_to(REPO_ROOT).as_posix()}"
    argv = [
        sys.executable,
        str(RUNNER),
        "run",
        "--input",
        str(declared_input.relative_to(REPO_ROOT)),
        str(exp_dir.relative_to(REPO_ROOT)),
        "--",
        "/bin/bash",
        sandbox_script,
        "argument with spaces",
        "",
    ]
    return Case(exp_dir=exp_dir, argv=argv)


def validate_case(case: Case, result: subprocess.CompletedProcess[str]) -> int:
    """Validate expected empty-trace status and durable sandbox evidence."""

    (case.exp_dir / "runner.stdout.log").write_text(result.stdout, encoding="utf-8")
    (case.exp_dir / "runner.stderr.log").write_text(result.stderr, encoding="utf-8")
    require(result.returncode == 1, f"expected empty-trace status 1, got {result.returncode}")
    require(not (case.exp_dir / "failures.txt").read_text(encoding="utf-8").strip(), "sandbox assertions failed")
    require((case.exp_dir / "exp-write.txt").is_file(), "EXP write did not reach Host")
    require(not (case.exp_dir / "pilot-ephemeral.txt").exists(), "sandbox /tmp leaked into EXP")
    require((case.exp_dir / "rollout.jsonl").is_file(), "synthetic rollout was not collected")
    manifest = json.loads((case.exp_dir / "artifact_manifest.json").read_text(encoding="utf-8"))
    require(manifest["final_status"] == 1, "manifest did not record final status 1")
    require((case.exp_dir / ".codex-upper").is_dir(), "failed run did not preserve isolated state")
    log = (case.exp_dir / ".claude-tap.log").read_text(encoding="utf-8", errors="replace")
    match = READY_PATTERN.search(log)
    require(match is not None, "tap ready port missing from log")
    return int(match.group(1))


def clean_case(case: Case) -> None:
    """Run idempotent postmortem cleanup and verify only isolated state is removed."""

    for _ in range(2):
        result = subprocess.run(
            [sys.executable, str(RUNNER), "clean", str(case.exp_dir.relative_to(REPO_ROOT))],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        require(result.returncode == 0, f"clean failed: {result.stderr}")
    require(not (case.exp_dir / ".codex-upper").exists(), "clean left isolated state")
    require((case.exp_dir / "artifact_manifest.json").is_file(), "clean removed durable artifacts")


def check_preflight_manifest(group_dir: Path, declared_input: Path) -> None:
    """Prove bwrap preflight failure happens before tap and still manifests."""

    exp_dir = group_dir / "preflight-missing-token"
    result = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "run",
            "--input",
            str(declared_input.relative_to(REPO_ROOT)),
            str(exp_dir.relative_to(REPO_ROOT)),
            "--",
            "/bin/true",
        ],
        cwd=REPO_ROOT,
        env=runner_env(VENUS_TOKEN=""),
        check=False,
        capture_output=True,
        text=True,
    )
    require(result.returncode == 1, "preflight failure did not return 1")
    require((exp_dir / "artifact_manifest.json").is_file(), "preflight manifest missing")
    require(not (exp_dir / ".claude-tap.log").exists(), "tap started before bwrap preflight")


def check_clean_rejections(group_dir: Path) -> None:
    """Prove clean rejects outputs root and paths outside outputs."""

    sentinel = REPO_ROOT / "do-not-delete-pilot-clean-sentinel"
    sentinel.write_text("keep\n", encoding="utf-8")
    try:
        for target in (REPO_ROOT / "outputs", sentinel):
            result = subprocess.run(
                [sys.executable, str(RUNNER), "clean", str(target)],
                cwd=REPO_ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            require(result.returncode == 1, f"clean accepted unsafe target: {target}")
        require(sentinel.read_text(encoding="utf-8") == "keep\n", "unsafe clean changed sentinel")
    finally:
        sentinel.unlink(missing_ok=True)


def check_gateway() -> None:
    """Start the real gateway in a clean environment without making a request."""

    result = subprocess.run(
        [str(GATEWAY), "sol", "--version"],
        cwd=REPO_ROOT,
        env={
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "VENUS_TOKEN": "cvm-half-integration-dummy",
            "CLAUDE_TAP_URL": "http://127.0.0.1:19999/v1",
        },
        check=False,
        capture_output=True,
        text=True,
    )
    require(result.returncode == 0, f"gateway/Codex no-network start failed: {result.stderr}")
    require("codex-cli" in result.stdout, "gateway did not execute PATH Codex")


def main() -> int:
    """Run the real-bwrap matrix and print a compact JSON handoff."""

    require(sys.platform.startswith("linux"), "this integration test is CVM/Linux-only")
    require(shutil.which("bwrap") == "/usr/bin/bwrap", "expected /usr/bin/bwrap")
    require(shutil.which("codex") == "/usr/bin/codex", "expected /usr/bin/codex")
    require(shutil.which("claude-tap") is not None, "claude-tap is missing")
    declared_input, other_input = choose_inputs()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    group_dir = REPO_ROOT / "outputs" / f"{stamp}-tap-v4-half"
    group_dir.mkdir(parents=True, exist_ok=True)
    baseline = {"bwrap": process_ids("bwrap"), "claude-tap": process_ids("claude-tap")}
    sleeper = subprocess.Popen(
        ["sleep", "180"],
        env={**os.environ, "HOST_PROCESS_SECRET": "must-stay-host-only"},
        start_new_session=True,
    )
    cases: list[Case] = []
    active_processes: list[subprocess.Popen[str]] = []
    try:
        check_gateway()
        check_preflight_manifest(group_dir, declared_input)
        check_clean_rejections(group_dir)

        first = prepare_case(group_dir, "parallel-a", declared_input, other_input, sleeper.pid)
        second = prepare_case(group_dir, "parallel-b", declared_input, other_input, sleeper.pid)
        cases.extend([first, second])
        processes = [
            subprocess.Popen(
                case.argv,
                cwd=REPO_ROOT,
                env=runner_env(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for case in cases
        ]
        active_processes.extend(processes)
        results = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=90)
            results.append(subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr))
            active_processes.remove(process)
        ports = [validate_case(case, result) for case, result in zip(cases, results, strict=True)]
        require(len(set(ports)) == len(ports), "concurrent pilots reused one tap port")

        signal_case = prepare_case(
            group_dir,
            "signal-int",
            declared_input,
            other_input,
            sleeper.pid,
            sleep_seconds=90,
        )
        cases.append(signal_case)
        process = subprocess.Popen(
            signal_case.argv,
            cwd=REPO_ROOT,
            env=runner_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        active_processes.append(process)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not (signal_case.exp_dir / "workload-started.txt").exists():
            require(process.poll() is None, "signal case exited before workload start")
            time.sleep(0.1)
        require((signal_case.exp_dir / "workload-started.txt").exists(), "signal workload did not start")
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=30)
        active_processes.remove(process)
        (signal_case.exp_dir / "runner.stdout.log").write_text(stdout, encoding="utf-8")
        (signal_case.exp_dir / "runner.stderr.log").write_text(stderr, encoding="utf-8")
        require(process.returncode == 130, f"SIGINT status was {process.returncode}, expected 130")
        require((signal_case.exp_dir / "signal-received.txt").is_file(), "workload group missed SIGINT")
        require((signal_case.exp_dir / "rollout.jsonl").is_file(), "signal case rollout missing")

        for case in cases:
            clean_case(case)
        time.sleep(0.5)
        for name, before in baseline.items():
            require(process_ids(name) <= before, f"orphan {name} process remained")

        summary = {
            "group": str(group_dir.relative_to(REPO_ROOT)),
            "parallel_ports": ports,
            "parallel_statuses": [result.returncode for result in results],
            "signal_status": process.returncode,
            "cap_eff": (first.exp_dir / "cap-eff.txt").read_text(encoding="utf-8").strip(),
            "claude_tap": subprocess.run(
                ["claude-tap", "--version"], check=True, capture_output=True, text=True
            ).stdout.strip(),
            "codex": subprocess.run(
                ["codex", "--version"], check=True, capture_output=True, text=True
            ).stdout.strip(),
        }
        (group_dir / "half-integration-summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(summary, sort_keys=True))
        return 0
    finally:
        for process in active_processes:
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        if sleeper.poll() is None:
            os.killpg(sleeper.pid, signal.SIGTERM)
            sleeper.wait(timeout=5)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CheckFailure, OSError, subprocess.SubprocessError) as exc:
        print(f"pilot-runner-cvm: {exc}", file=sys.stderr)
        raise SystemExit(1)
