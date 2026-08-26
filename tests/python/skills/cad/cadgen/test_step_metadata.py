import unittest
from pathlib import Path

from cadgen._internal.step_metadata import (
    TEXT_TO_CAD_GENERATOR,
    inject_text_to_cad_step_metadata,
    read_text_to_cad_step_metadata,
)
from tests.python.support.tmp_root import temporary_directory


MINIMAL_STEP = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('Open CASCADE Model'),'2;1');
ENDSEC;
DATA;
#1=PRODUCT_DEFINITION('design','',#2,#3);
#4=PRODUCT_DEFINITION_SHAPE('','',#1);
#5=SHAPE_REPRESENTATION('',(#6),#7);
#7=(GEOMETRIC_REPRESENTATION_CONTEXT(3) REPRESENTATION_CONTEXT('Context #1','3D'));
ENDSEC;
END-ISO-10303-21;
"""


class TextToCadStepMetadataTests(unittest.TestCase):
    def test_injects_and_reads_text_to_cad_metadata(self) -> None:
        with temporary_directory(prefix="cad-step-metadata-") as temp_dir:
            step_path = Path(temp_dir) / "fixture.step"
            step_path.write_text(MINIMAL_STEP, encoding="utf-8")

            inject_text_to_cad_step_metadata(
                step_path,
                entry_kind="assembly",
                source_hash="source-hash-123",
            )

            metadata = read_text_to_cad_step_metadata(step_path)
            self.assertEqual(TEXT_TO_CAD_GENERATOR, metadata.get("generator"))
            self.assertEqual("assembly", metadata.get("entryKind"))
            self.assertEqual("source-hash-123", metadata.get("sourceHash"))
            step_text = step_path.read_text(encoding="utf-8")
            self.assertIn("PROPERTY_DEFINITION('cadgen metadata','cadgen:entryKind'", step_text)

    def test_reads_tail_metadata_without_full_file_scan(self) -> None:
        with temporary_directory(prefix="cad-step-metadata-tail-") as temp_dir:
            step_path = Path(temp_dir) / "large-fixture.step"
            metadata_block = "\n".join(
                [
                    "#100=DESCRIPTIVE_REPRESENTATION_ITEM('cadgen:generator','cadgen');",
                    "#101=REPRESENTATION('cadgen:generator',(#100),#7);",
                    "#102=PROPERTY_DEFINITION('cadgen metadata','cadgen:generator',#1);",
                    "#103=PROPERTY_DEFINITION_REPRESENTATION(#102,#101);",
                    "#104=DESCRIPTIVE_REPRESENTATION_ITEM('cadgen:entryKind','assembly');",
                    "#105=REPRESENTATION('cadgen:entryKind',(#104),#7);",
                    "#106=PROPERTY_DEFINITION('cadgen metadata','cadgen:entryKind',#1);",
                    "#107=PROPERTY_DEFINITION_REPRESENTATION(#106,#105);",
                    "#112=DESCRIPTIVE_REPRESENTATION_ITEM('cadgen:sourceHash','source-hash-tail');",
                    "#113=REPRESENTATION('cadgen:sourceHash',(#112),#7);",
                    "#114=PROPERTY_DEFINITION('cadgen metadata','cadgen:sourceHash',#1);",
                    "#115=PROPERTY_DEFINITION_REPRESENTATION(#114,#113);",
                ]
            )
            step_path.write_text(
                "ISO-10303-21;\nDATA;\n"
                + ("#9=PRODUCT('padding','padding','',(#7));\n" * 40000)
                + metadata_block
                + "\nENDSEC;\nEND-ISO-10303-21;\n",
                encoding="utf-8",
            )

            metadata = read_text_to_cad_step_metadata(step_path)

            self.assertEqual(TEXT_TO_CAD_GENERATOR, metadata.get("generator"))
            self.assertEqual("assembly", metadata.get("entryKind"))
            self.assertEqual("source-hash-tail", metadata.get("sourceHash"))


    def test_tail_injection_never_collides_with_ids_outside_the_tail(self) -> None:
        # Regression: the fast path took its max entity id from the tail alone while the
        # refs came from the head, so a file whose higher ids sit outside the tail got
        # injections colliding with entities nobody scanned. Windows are COMPUTED here to
        # reproduce that shape on a tiny fixture: head+tail overlap (so this is the fast
        # path, not the whole-file fallback), #900 lives only in the head, and #9 sits
        # ready to be collided with by a tail-only scan that mints from #8.
        import re as re_module
        from unittest import mock

        from cadgen._internal import step_metadata

        with temporary_directory(prefix="cad-step-metadata-head-ids-") as temp_dir:
            step_path = Path(temp_dir) / "head-heavy.step"
            text = (
                "ISO-10303-21;\n"
                "HEADER;\nFILE_DESCRIPTION(('x'),'2;1');\nENDSEC;\n"
                "DATA;\n"
                "#1=PRODUCT_DEFINITION('design','',#2,#3);\n"
                "#2=PRODUCT('base','base','',(#7));\n"
                "#900=CARTESIAN_POINT('high-id-in-head',(0.,0.,0.));\n"
                "#9=CARTESIAN_POINT('collision-target',(0.,0.,0.));\n"
                "#4=PRODUCT_DEFINITION_SHAPE('','',#1);\n"
                "#5=SHAPE_REPRESENTATION('',(#6),#7);\n"
                "#6=CARTESIAN_POINT('p',(0.,0.,0.));\n"
                "#7=(GEOMETRIC_REPRESENTATION_CONTEXT(3) REPRESENTATION_CONTEXT('c','3D'));\n"
                "#8=CARTESIAN_POINT('tail-zone',(0.,0.,0.));\n"
                "ENDSEC;\n"
                "END-ISO-10303-21;\n"
            )
            step_path.write_text(text, encoding="utf-8")

            idx_high = text.index("#900")
            idx_tail_start = text.index("#8=")
            tail_bytes = len(text) - idx_tail_start  # covers from #8 to EOF
            head_bytes = len(text) - tail_bytes  # exactly meets the tail: full coverage
            # Premises: the windows COVER the file (the fast path may decide), yet the
            # tail alone never sees #900.
            self.assertEqual(head_bytes + tail_bytes, len(text))
            self.assertLess(idx_high, len(text) - tail_bytes)

            original_tail = step_metadata._read_step_tail_payload
            original_head = step_metadata._read_step_head_text

            def small_tail(path, **kwargs):
                return original_tail(path, tail_bytes=tail_bytes)

            def small_head(path, **kwargs):
                return original_head(path, head_bytes=head_bytes)

            with mock.patch.object(step_metadata, "_read_step_tail_payload", small_tail), \
                    mock.patch.object(step_metadata, "_read_step_head_text", small_head):
                inject_text_to_cad_step_metadata(
                    step_path,
                    entry_kind="assembly",
                    source_hash="source-hash-head",
                )

            metadata = read_text_to_cad_step_metadata(step_path)
            self.assertEqual(TEXT_TO_CAD_GENERATOR, metadata.get("generator"))
            self.assertEqual("source-hash-head", metadata.get("sourceHash"))

            final_text = step_path.read_text(encoding="utf-8")
            entity_ids = [int(m) for m in re_module.findall(r"(?m)^#(\d+)=", final_text)]
            self.assertEqual(len(entity_ids), len(set(entity_ids)), "injected a colliding entity id")
            self.assertGreater(min(entity_ids[-10:]), 900)

    def test_a_file_with_an_unscanned_middle_takes_the_whole_file_path(self) -> None:
        # Head and tail windows that do NOT cover the file must not decide at all: the
        # fast path has to bail to the whole-file rewrite, which sees the buried #950 and
        # mints above it instead of colliding with the #9 parked between the windows.
        import re as re_module
        from unittest import mock

        from cadgen._internal import step_metadata

        with temporary_directory(prefix="cad-step-metadata-middle-") as temp_dir:
            step_path = Path(temp_dir) / "middle.step"
            text = (
                "ISO-10303-21;\n"
                "HEADER;\nFILE_DESCRIPTION(('x'),'2;1');\nENDSEC;\n"
                "DATA;\n"
                "#1=PRODUCT_DEFINITION('design','',#2,#3);\n"
                "#2=PRODUCT('base','base','',(#7));\n"
                "#4=PRODUCT_DEFINITION_SHAPE('','',#1);\n"
                "#5=SHAPE_REPRESENTATION('',(#6),#7);\n"
                "#6=CARTESIAN_POINT('p',(0.,0.,0.));\n"
                "#7=(GEOMETRIC_REPRESENTATION_CONTEXT(3) REPRESENTATION_CONTEXT('c','3D'));\n"
                "#9=CARTESIAN_POINT('collision-target',(0.,0.,0.));\n"
                "#950=CARTESIAN_POINT('buried-in-the-middle',(0.,0.,0.));\n"
                "#8=CARTESIAN_POINT('tail-zone',(0.,0.,0.));\n"
                "ENDSEC;\n"
                "END-ISO-10303-21;\n"
            )
            step_path.write_text(text, encoding="utf-8")

            # The head window must hold VALID refs (so the old fast path proceeds
            # past ref resolution) yet stop short of the #9 target and buried #950.
            head_bytes = text.index("#9=")
            tail_bytes = len(text) - text.index("#8=")
            # Premise: a real middle exists that neither window scans.
            self.assertGreater(len(text), head_bytes + tail_bytes)

            original_tail = step_metadata._read_step_tail_payload
            original_head = step_metadata._read_step_head_text

            def tiny_tail(path, **kwargs):
                return original_tail(path, tail_bytes=tail_bytes)

            def tiny_head(path, **kwargs):
                return original_head(path, head_bytes=head_bytes)

            with mock.patch.object(step_metadata, "_read_step_tail_payload", tiny_tail), \
                    mock.patch.object(step_metadata, "_read_step_head_text", tiny_head):
                inject_text_to_cad_step_metadata(
                    step_path,
                    entry_kind="part",
                    source_hash="source-hash-middle",
                )

            metadata = read_text_to_cad_step_metadata(step_path)
            self.assertEqual("part", metadata.get("entryKind"))
            entity_ids = [int(m) for m in re_module.findall(r"(?m)^#(\d+)=", step_path.read_text(encoding="utf-8"))]
            self.assertEqual(len(entity_ids), len(set(entity_ids)), "whole-file fallback collided")
            self.assertGreater(min(entity_ids[-10:]), 950)


if __name__ == "__main__":
    unittest.main()

    unittest.main()
