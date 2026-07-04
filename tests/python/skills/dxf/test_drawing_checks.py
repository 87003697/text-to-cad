"""Tests for the generation-time DXF drawing checks."""

import unittest
from pathlib import Path

import ezdxf

from cadgen.drawing_checks import (
    DrawingValidationError,
    raise_on_error_findings,
    validate_drawing_document,
    validate_dxf_file,
)
from tests.python.support.tmp_root import temporary_directory


def _new_document():
    document = ezdxf.new("R2010")
    document.units = ezdxf.units.MM
    return document


def _codes(findings):
    return sorted(finding.code for finding in findings)


class DrawingChecksTests(unittest.TestCase):
    def test_closed_profiles_and_bend_lines_pass(self) -> None:
        document = _new_document()
        modelspace = document.modelspace()
        modelspace.add_lwpolyline([(0, 0), (40, 0), (40, 20), (0, 20)], close=True, dxfattribs={"layer": "CUT"})
        modelspace.add_circle((20, 10), 3, dxfattribs={"layer": "CUT"})
        modelspace.add_line((10, 0), (10, 20), dxfattribs={"layer": "BEND"})

        self.assertEqual([], validate_drawing_document(document))

    def test_open_polyline_on_cut_layer_is_error(self) -> None:
        document = _new_document()
        document.modelspace().add_lwpolyline([(0, 0), (40, 0), (40, 20)], dxfattribs={"layer": "CUT"})

        self.assertIn("open_cut_profile", _codes(validate_drawing_document(document)))

    def test_chained_lines_closing_a_loop_pass(self) -> None:
        document = _new_document()
        modelspace = document.modelspace()
        for start, end in [((0, 0), (10, 0)), ((10, 0), (10, 10)), ((10, 10), (0, 0))]:
            modelspace.add_line(start, end, dxfattribs={"layer": "CUT"})

        self.assertEqual([], validate_drawing_document(document))

    def test_dangling_line_on_cut_layer_is_error(self) -> None:
        document = _new_document()
        modelspace = document.modelspace()
        modelspace.add_lwpolyline([(0, 0), (40, 0), (40, 20), (0, 20)], close=True, dxfattribs={"layer": "CUT"})
        modelspace.add_line((100, 100), (120, 100), dxfattribs={"layer": "CUT"})

        self.assertIn("open_cut_profile", _codes(validate_drawing_document(document)))

    def test_zero_length_and_duplicate_entities_are_errors(self) -> None:
        document = _new_document()
        modelspace = document.modelspace()
        modelspace.add_lwpolyline([(0, 0), (40, 0), (40, 20), (0, 20)], close=True, dxfattribs={"layer": "CUT"})
        modelspace.add_line((5, 5), (5, 5), dxfattribs={"layer": "BEND"})
        modelspace.add_circle((20, 10), 3, dxfattribs={"layer": "CUT"})
        modelspace.add_circle((20, 10), 3, dxfattribs={"layer": "CUT"})

        codes = _codes(validate_drawing_document(document))
        self.assertIn("zero_length_entity", codes)
        self.assertIn("duplicate_entity", codes)

    def test_empty_drawing_is_error(self) -> None:
        document = _new_document()

        self.assertIn("empty_drawing", _codes(validate_drawing_document(document)))

    def test_raise_on_error_findings(self) -> None:
        document = _new_document()
        findings = validate_drawing_document(document)

        with self.assertRaises(DrawingValidationError):
            raise_on_error_findings(findings, label="empty")

    def test_validate_dxf_file_roundtrip(self) -> None:
        document = _new_document()
        document.modelspace().add_lwpolyline(
            [(0, 0), (40, 0), (40, 20), (0, 20)], close=True, dxfattribs={"layer": "CUT"}
        )
        with temporary_directory(prefix="dxf-checks") as root:
            path = Path(root) / "outline.dxf"
            document.saveas(str(path))

            self.assertEqual([], validate_dxf_file(path))


if __name__ == "__main__":
    unittest.main()
