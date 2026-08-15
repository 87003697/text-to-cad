"""Every artifact rename retries the one Windows error that is worth retrying.

A cached artifact is written to a temp file and renamed into place. On a Windows SMB share that
rename can lose to ``WinError 32`` -- the redirector still holds the handle Python just closed --
which under a parallel component build fails reliably (issue #241: 8 workers failed every time
on a NAS, one worker succeeded). PR #244 fixed the rename inside the GLB writer; this pins the
policy for all of them, because there are seven in a build's write path and hardening one moves
the failure to the next.
"""

from __future__ import annotations

import os
import re
import unittest
from pathlib import Path
from unittest import mock

from tests.python.support.paths import REPO_ROOT, add_repo_path

add_repo_path("packages/cadgen/src")

from cadgen._internal import atomic_replace

CADGEN_SRC = REPO_ROOT / "packages" / "cadgen" / "src" / "cadgen"


def sharing_violation() -> PermissionError:
    error = PermissionError(13, "file is being used by another process")
    error.winerror = atomic_replace.WINDOWS_SHARING_VIOLATION
    return error


class ReplaceAtomicTest(unittest.TestCase):
    def test_a_sharing_violation_is_retried_until_it_wins(self) -> None:
        attempts = []

        def flaky(source, target):
            attempts.append((source, target))
            if len(attempts) < 3:
                raise sharing_violation()

        with mock.patch.object(atomic_replace.os, "replace", side_effect=flaky), \
             mock.patch.object(atomic_replace.time, "sleep") as sleep:
            atomic_replace.replace_atomic("from.tmp", "to.glb")

        self.assertEqual(3, len(attempts))
        self.assertEqual(
            [(0.05,), (0.1,)],
            [call.args for call in sleep.call_args_list],
            "the backoff must grow, and must not sleep after the winning attempt",
        )

    def test_the_window_covers_the_measured_tail_not_the_median(self) -> None:
        """Issue #274, and the reason this number is not free to shrink.

        Measured on a Synology SMB share over two 84-component builds: 126 of 168 renames
        blocked at all, median 31 ms, p90 118 ms, max 389 ms. The budget is spent per rename
        but the BUILD only succeeds if every one of them wins, so the window has to clear the
        tail rather than the middle -- at 168 draws, a 1% per-rename loss rate is roughly a
        1-in-5 chance of a clean build.
        """
        self.assertGreater(
            sum(atomic_replace.RETRY_DELAYS_SECONDS),
            0.389,
            "the retry window must outlast the longest rename actually measured on SMB",
        )
        # ...and the first retry still has to be short, or the median rename pays for the tail.
        self.assertLessEqual(atomic_replace.RETRY_DELAYS_SECONDS[0], 0.05)

    def test_it_gives_up_rather_than_hanging(self) -> None:
        # A rename that cannot win inside the window is not a deferred close. Failing beats a
        # build that retries forever.
        error = sharing_violation()
        with mock.patch.object(atomic_replace.os, "replace", side_effect=error), \
             mock.patch.object(atomic_replace.time, "sleep") as sleep:
            with self.assertRaises(PermissionError) as raised:
                atomic_replace.replace_atomic("from.tmp", "to.glb")
        self.assertIs(error, raised.exception, "the original error must survive the retries")
        self.assertEqual(len(atomic_replace.RETRY_DELAYS_SECONDS), sleep.call_count)

    def test_every_other_error_surfaces_at_once(self) -> None:
        for winerror in (5, 2, None):
            error = PermissionError(13, "denied")
            if winerror is not None:
                error.winerror = winerror
            with self.subTest(winerror=winerror):
                with mock.patch.object(atomic_replace.os, "replace", side_effect=error), \
                     mock.patch.object(atomic_replace.time, "sleep") as sleep:
                    with self.assertRaises(PermissionError):
                        atomic_replace.replace_atomic("from.tmp", "to.glb")
                sleep.assert_not_called()

    def test_the_happy_path_does_not_sleep(self) -> None:
        with mock.patch.object(atomic_replace.os, "replace") as replace, \
             mock.patch.object(atomic_replace.time, "sleep") as sleep:
            atomic_replace.replace_atomic("from.tmp", "to.glb")
        replace.assert_called_once_with("from.tmp", "to.glb")
        sleep.assert_not_called()


class WriteBytesAtomicTest(unittest.TestCase):
    def test_it_writes_through_a_temp_file_in_the_SAME_directory(self) -> None:
        # A temp file on another volume would turn the rename into a copy, which is not atomic.
        import tempfile

        with tempfile.TemporaryDirectory(prefix="cad-atomic-") as temp_dir:
            target = Path(temp_dir) / "nested" / "artifact.glb"
            seen = {}

            real_replace = os.replace

            def record(source, destination):
                seen["source_parent"] = Path(source).parent
                real_replace(source, destination)

            with mock.patch.object(atomic_replace.os, "replace", side_effect=record):
                atomic_replace.write_bytes_atomic(target, b"glTF")

            self.assertEqual(b"glTF", target.read_bytes())
            self.assertEqual(target.parent, seen["source_parent"])

    def test_the_temp_file_does_not_survive_a_failure(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory(prefix="cad-atomic-") as temp_dir:
            target = Path(temp_dir) / "artifact.glb"
            with mock.patch.object(atomic_replace.os, "replace", side_effect=OSError("nope")):
                with self.assertRaises(OSError):
                    atomic_replace.write_bytes_atomic(target, b"glTF")
            self.assertEqual([], list(Path(temp_dir).iterdir()), "a temp file was left behind")


class EveryRenameGoesThroughTheHelperTest(unittest.TestCase):
    """The point of the helper: one policy, not one per writer.

    Hardening a single rename is what left the reporter's next build to fail somewhere else, so
    a direct ``os.replace`` in cadgen is the regression this test exists to catch.
    """

    def test_no_cadgen_module_renames_directly(self) -> None:
        offenders = []
        for path in sorted(CADGEN_SRC.rglob("*.py")):
            if path.name == "atomic_replace.py" or "__pycache__" in path.parts:
                continue
            source = re.sub(r"#[^\n]*", "", path.read_text(encoding="utf-8"))
            if re.search(r"\bos\.replace\(|\.replace\(\s*target", source):
                offenders.append(str(path.relative_to(REPO_ROOT)))
        self.assertEqual(
            [],
            offenders,
            "use cadgen._internal.atomic_replace.replace_atomic: a bare os.replace loses to "
            "WinError 32 on an SMB share (issue #241)",
        )


if __name__ == "__main__":
    unittest.main()
