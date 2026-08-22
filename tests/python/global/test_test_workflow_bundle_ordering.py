"""Guard the Linux ``test`` workflow bundle/materialize/check ordering.

The production symlink-free assertion in
``scripts/github-workflows/check-builds.sh`` fires against whatever tree the
step sees. The Linux ``Test`` job asserts that same invariant, so it must
materialize symlinked generated outputs before it bundles -- otherwise a
skill that references cross-package sources through the checked-in
``skills/*/scripts/packages/*`` symlink will look fine in development and
still ship a broken bundle to Codex users (Codex plugin ``add`` silently
drops symlinks; see ``scripts/github-workflows/check-builds.sh`` for the
full incident note).

This test locks the ordering so a future workflow edit that reintroduces
the pre-materialize bundle sequence trips a red test rather than a red CI
run three days later.
"""

from __future__ import annotations

from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[3]
TEST_WORKFLOW = REPO_ROOT / ".github/workflows/test.yml"


def _job_step_lines(text: str, job: str) -> list[int]:
    """Return the line indices (1-based) of ``- name:`` entries in ``job``."""

    lines = text.splitlines()
    in_job = False
    steps: list[int] = []
    job_indent: int | None = None
    steps_indent: int | None = None
    for index, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if stripped == f"{job}:":
            in_job = True
            job_indent = len(raw) - len(raw.lstrip(" "))
            continue
        if not in_job:
            continue
        if raw and not raw.startswith(" ") and stripped.endswith(":"):
            break
        if job_indent is not None and stripped.endswith(":") and not stripped.startswith("-"):
            current_indent = len(raw) - len(raw.lstrip(" "))
            if current_indent <= job_indent and stripped != f"{job}:":
                break
        if stripped.startswith("- name:"):
            steps_indent = len(raw) - len(raw.lstrip(" "))
            if steps_indent is None or steps_indent <= (job_indent or 0):
                # Not a step under this job.
                continue
            steps.append(index)
    return steps


class TestWorkflowBundleOrderingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = TEST_WORKFLOW.read_text(encoding="utf-8")

    def _step_index(self, needle: str) -> int:
        lines = self.text.splitlines()
        for index, line in enumerate(lines, start=1):
            stripped = line.strip()
            if stripped.startswith("- name:") and needle in stripped:
                return index
        self.fail(f"Workflow is missing a step matching: {needle!r}")

    def test_materialize_runs_between_freshness_and_bundle(self) -> None:
        freshness = self._step_index("Check generated outputs against sources")
        materialize = self._step_index("Materialize production bundle layout")
        bundle = self._step_index("Bundle production outputs")
        layout = self._step_index("Check production bundle layout")

        self.assertLess(
            freshness,
            materialize,
            "Development freshness check must run before materialization; "
            "otherwise the symlink layout is already dereferenced when the "
            "check runs.",
        )
        self.assertLess(
            materialize,
            bundle,
            "Production materialization must run before the bundle step so "
            "the bundle populates a symlink-free layout, matching the "
            "release workflow ordering.",
        )
        self.assertLess(
            bundle,
            layout,
            "Production bundle must run before check-builds.sh reads the "
            "generated outputs.",
        )

    def test_materialize_uses_the_shared_release_script(self) -> None:
        materialize = self._step_index("Materialize production bundle layout")
        # The command line for the step follows the ``- name:`` header; the
        # release workflow uses the same script, so re-use it verbatim.
        block = "\n".join(
            self.text.splitlines()[materialize - 1 : materialize + 3]
        )
        self.assertIn(
            "scripts/bundle/materialize-production-layout.sh",
            block,
            "Test workflow must call the same materialization script as "
            "release.yml so the two paths cannot diverge.",
        )


if __name__ == "__main__":
    unittest.main()
