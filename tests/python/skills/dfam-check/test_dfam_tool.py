#!/usr/bin/env python3

"""Measurement tests for the dfam-check tool.

Each part is built with build123d so the correct answer is known from the
construction rather than from a recorded run: a 2 mm wall is 2 mm because it
was built that way, and a 30 deg underside is below a 45 deg limit by
geometry. A regression then shows up as a disagreement with the model, not as
a diff against whatever the tool happened to print last time.
"""

from __future__ import annotations

import contextlib
import io
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from build123d import Box, Polyline, Pos, Rotation, export_stl, extrude, make_face

from tests.python.support.paths import add_repo_path

add_repo_path("skills/dfam-check/scripts")

import dfam_tool  # noqa: E402


def _stl(part, tmp: Path, name: str) -> str:
    path = tmp / f"{name}.stl"
    export_stl(part, str(path))
    return str(path)


def _wedge(angle_deg: float, length: float = 30.0, width: float = 12.0):
    """Cantilever whose underside rises at angle_deg from horizontal.

    Vertex order matters: built the other way up this is a ramp, which is
    self-supporting and tests nothing.
    """
    h = max(length * math.tan(math.radians(angle_deg)), 0.6)
    pts = [(0, 0, 0), (length, h, 0), (0, h, 0), (0, 0, 0)]
    return Rotation(90, 0, 0) * extrude(make_face(Polyline(*pts)), width)


def _hollow_box(wall: float, outer: float = 24.0, height: float = 16.0):
    inner = max(outer - 2 * wall, 0.5)
    return Box(outer, outer, height) - Pos(0, 0, wall) * Box(inner, inner, height)


class MeshFactsTest(unittest.TestCase):
    def test_solid_box_is_watertight_single_body(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mesh = dfam_tool._load(_stl(Box(20, 20, 15), tmp, "box"))
            facts = dfam_tool._mesh_facts(mesh)

        self.assertTrue(facts["watertight"])
        self.assertEqual(facts["body_count"], 1)
        self.assertEqual(facts["euler_number"], 2)
        self.assertAlmostEqual(facts["volume_mm3"], 6000.0, delta=1.0)


class OverhangTest(unittest.TestCase):
    def test_underside_below_limit_is_flagged_above_limit_is_not(self) -> None:
        """A 30 deg underside is a violation at 45 deg and clear at 20 deg."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            path = _stl(_wedge(30.0), tmp, "wedge30")
            mesh = dfam_tool._load(path)
            strict = dfam_tool._overhang_facts(mesh, 45.0)
            lenient = dfam_tool._overhang_facts(mesh, 20.0)

        self.assertGreater(strict["down_facing_area_below_limit_mm2"], 0.0)
        self.assertEqual(lenient["down_facing_area_below_limit_mm2"], 0.0)
        self.assertEqual(strict["angle_limit_used_deg"], 45.0)

    def test_face_resting_on_plate_is_not_counted(self) -> None:
        """The base is carried by the bed, so a plain box needs no support."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mesh = dfam_tool._load(_stl(Box(20, 20, 15), tmp, "box"))
            facts = dfam_tool._overhang_facts(mesh, 45.0)

        self.assertEqual(facts["down_facing_area_below_limit_mm2"], 0.0)
        self.assertEqual(facts["face_count_below_limit"], 0)


class WallThicknessTest(unittest.TestCase):
    def test_measured_wall_matches_constructed_wall(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mesh = dfam_tool._load(_stl(_hollow_box(2.0), tmp, "wall2"))
            facts = dfam_tool._wall_facts(mesh, samples=600)

        self.assertAlmostEqual(facts["median_mm"], 2.0, delta=0.4)

    def test_thinner_wall_measures_thinner(self) -> None:
        """Ordering must hold even where absolute sampling error does not."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            thin = dfam_tool._wall_facts(
                dfam_tool._load(_stl(_hollow_box(0.8), tmp, "wall08")), samples=600
            )
            thick = dfam_tool._wall_facts(
                dfam_tool._load(_stl(_hollow_box(3.0), tmp, "wall30")), samples=600
            )

        self.assertLess(thin["median_mm"], thick["median_mm"])


class OrientationTest(unittest.TestCase):
    def test_flipping_a_wedge_removes_its_support_requirement(self) -> None:
        """The same wedge is self-supporting once turned over.

        This is the whole point of ranking orientations, so if it stops
        holding, the ranking is not doing anything useful.
        """
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mesh = dfam_tool._load(_stl(_wedge(30.0), tmp, "wedge30"))
            facts = dfam_tool._orientation_facts(mesh, 45.0)

        candidates = facts["candidates"]
        self.assertEqual(len(candidates), 6)

        current = next(c for c in candidates if c["orientation"] == "current_plus_z")
        best = min(candidates, key=lambda c: c["support_area_mm2"])

        self.assertGreater(current["support_area_mm2"], 0.0)
        self.assertEqual(best["support_area_mm2"], 0.0)
        for candidate in candidates:
            self.assertGreater(candidate["build_height_mm"], 0.0)


def _run_cli(argv: list[str]) -> dict:
    """Invoke the CLI the way a skill run does and parse what it prints."""
    buf = io.StringIO()
    with mock.patch.object(sys, "argv", ["dfam_tool.py", *argv]):
        with contextlib.redirect_stdout(buf):
            dfam_tool.main()
    return json.loads(buf.getvalue())


class CliTest(unittest.TestCase):
    def test_measure_emits_every_fact_family(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            path = _stl(Box(20, 20, 15), tmp, "box")
            payload = _run_cli(["measure", path, "--angle-limit", "45"])

        self.assertGreaterEqual(
            set(payload), {"file", "mesh", "overhangs", "wall_thickness"}
        )

    def test_angle_limit_is_echoed_back(self) -> None:
        """The limit used has to travel with the numbers it produced."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            path = _stl(Box(20, 20, 15), tmp, "box")
            payload = _run_cli(["measure", path, "--angle-limit", "30"])

        self.assertEqual(payload["overhangs"]["angle_limit_used_deg"], 30.0)

    def test_orientations_subcommand_emits_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            path = _stl(_wedge(30.0), tmp, "wedge30")
            payload = _run_cli(["orientations", path, "--angle-limit", "45"])

        self.assertEqual(len(payload["orientations"]["candidates"]), 6)


if __name__ == "__main__":
    unittest.main()
