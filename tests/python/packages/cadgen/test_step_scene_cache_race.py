"""Race handling when two writers commit the same STEP scene-cache entry.

The cache write lands via ``rename(temp_dir, cache_dir)``. Two processes loading the
same STEP race there: whoever finishes second renames onto the winner's POPULATED
directory, and POSIX reports that collision as ``OSError``/ENOTEMPTY -- not
FileExistsError, which is why the old ``except FileExistsError`` never fired where the
race actually happens and every collision fell through to the blanket handler. These
tests drive the commit helper directly with real directories, no OCP scene needed.
"""

from __future__ import annotations

import errno
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.python.support.paths import add_repo_path

add_repo_path("packages/cadgen/src")

from cadgen._internal.step_scene_cache import _commit_step_scene_cache_dir  # noqa: E402


class CommitStepSceneCacheDirTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _populated(self, name: str, marker: str) -> Path:
        directory = self.root / name
        directory.mkdir()
        (directory / "scene.json").write_text(marker, encoding="utf-8")
        return directory

    def test_a_losing_writer_yields_to_the_winner_and_drops_its_temp_dir(self):
        winner = self._populated("cache", "winner")
        loser = self._populated(".cache.loser.tmp", "loser")

        _commit_step_scene_cache_dir(loser, winner)

        self.assertEqual("winner", (winner / "scene.json").read_text(encoding="utf-8"))
        self.assertFalse(loser.exists(), "the loser's temp dir was left behind")

    def test_fileexistserror_is_reported_as_the_same_yield(self):
        # Windows spells the collision FileExistsError (errno EEXIST); pin that mapping
        # so a platform-specific spelling can never regress into the re-raise branch.
        winner = self._populated("cache", "winner")
        loser = self._populated(".cache.loser.tmp", "loser")
        with mock.patch.object(Path, "rename", side_effect=FileExistsError(errno.EEXIST, "exists")):
            _commit_step_scene_cache_dir(loser, winner)
        self.assertFalse(loser.exists())

    def test_an_empty_stale_target_is_replaced_by_the_commit(self):
        # An empty target (a crashed writer's remnant) does NOT collide on POSIX: the
        # rename replaces it. The commit must still go through, leaving exactly one
        # valid entry.
        stale = self.root / "cache"
        stale.mkdir()
        incoming = self._populated(".cache.incoming.tmp", "incoming")

        _commit_step_scene_cache_dir(incoming, stale)

        self.assertEqual("incoming", (stale / "scene.json").read_text(encoding="utf-8"))
        self.assertFalse(incoming.exists())

    def test_a_platform_that_cannot_rename_over_an_empty_remnant_lands_after_replacing_it(self):
        # Windows refuses a rename over ANY existing directory, empty included -- and
        # yielding there would wedge the entry forever (every load rewrites the temp
        # dir, collides with the empty remnant, and yields again, never caching).
        # The commit must replace a scene.json-less remnant and land.
        stale = self.root / "cache"
        stale.mkdir()
        incoming = self._populated(".cache.incoming.tmp", "incoming")
        real_rename = Path.rename
        attempts = {"count": 0}

        def refuse_first(path_self, target):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise OSError(errno.EEXIST, "exists")
            return real_rename(path_self, target)

        with mock.patch.object(Path, "rename", refuse_first):
            _commit_step_scene_cache_dir(incoming, stale)

        self.assertEqual("incoming", (stale / "scene.json").read_text(encoding="utf-8"))
        self.assertFalse(incoming.exists())

def test_the_commit_prunes_stale_sibling_entries_but_keeps_tmp_siblings(self):
    # Pruning runs only when THIS writer's commit lands (a yielding loser leaves the
    # directory alone), so the target here is an empty crashed-writer remnant.
    landed = self.root / "cache"
    landed.mkdir()
    stale_hash = self._populated("cache-old-hash", "stale")
    live_tmp = self.root / ".other.pid.tmp"
    live_tmp.mkdir()
    incoming = self._populated(".cache.incoming.tmp", "incoming")

    _commit_step_scene_cache_dir(incoming, landed)

    self.assertEqual("incoming", (landed / "scene.json").read_text(encoding="utf-8"))
    self.assertFalse(stale_hash.exists(), "stale hash entries must be pruned")
    self.assertTrue(live_tmp.exists(), ".tmp siblings belong to other writers")

    def test_an_unrelated_rename_error_is_not_swallowed_as_a_race(self):
        winner = self._populated("cache", "winner")
        loser = self._populated(".cache.loser.tmp", "loser")
        with mock.patch.object(Path, "rename", side_effect=OSError(errno.EIO, "io error")):
            with self.assertRaises(OSError) as raised:
                _commit_step_scene_cache_dir(loser, winner)
        self.assertEqual(errno.EIO, raised.exception.errno)


if __name__ == "__main__":
    unittest.main()