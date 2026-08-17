"""The Python and JS selector grammars are two implementations of one language.

`cadgen.cad_ref_syntax` and `cadjs/lib/cadRefs.js` parse the same refs, and before this fixture
existed nothing checked that they agreed -- the grammar was copy-pasted into four places. Both
suites read `packages/cadjs/src/lib/cadRefs.parity.json`, so a form added to one language and
forgotten in the other fails here rather than in a user's pasted ref.
"""

from __future__ import annotations

import json
import unittest

from tests.python.support.paths import repo_path

from cadgen.cad_ref_syntax import normalize_selector_list, parse_selector


FIXTURE_PATH = repo_path("packages", "cadjs", "src", "lib", "cadRefs.parity.json")


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


class ParityFixtureIsUsableTest(unittest.TestCase):
    def test_the_fixture_exists_and_has_all_three_case_kinds(self) -> None:
        data = _fixture()
        for key in ("selectorCases", "inheritanceCases", "aliasCases"):
            self.assertTrue(data.get(key), f"{key} must be present and non-empty")


class SelectorParityTest(unittest.TestCase):
    def test_every_selector_case_parses_as_the_fixture_says(self) -> None:
        for case in _fixture()["selectorCases"]:
            with self.subTest(selector=case["selector"], why=case.get("why", "")):
                parsed = parse_selector(case["selector"])
                self.assertIsNotNone(parsed)
                self.assertEqual(case["selectorType"], parsed.selector_type)
                self.assertEqual(case["occurrenceId"], parsed.occurrence_id)
                self.assertEqual(case["ordinal"], parsed.ordinal)
                self.assertEqual(case["canonical"], parsed.canonical)
                self.assertEqual(case.get("label", ""), parsed.label)


class InheritanceParityTest(unittest.TestCase):
    def test_comma_lists_inherit_as_the_fixture_says(self) -> None:
        for case in _fixture()["inheritanceCases"]:
            with self.subTest(input=case["input"], why=case.get("why", "")):
                self.assertEqual(case["expected"], normalize_selector_list(case["input"]))


class BackwardsCompatibilityTest(unittest.TestCase):
    """Adding label forms must not move a single existing ref.

    This is the guarantee that matters most: every numeric selector anyone has already pasted
    into an issue, a sidecar, or a script keeps parsing to exactly what it parsed to before.
    """

    NUMERIC_FORMS = (
        "o1",
        "o1.2",
        "o1.2.3.4.5.6",
        "o12.f19",
        "o1.2.s3",
        "o1.2.e3",
        "o1.2.v3",
        "f45",
        "s2",
        "e9",
        "v4",
        "#o2.f1",
        "m1",
        "m17",
    )

    def test_numeric_selectors_never_take_the_label_branch(self) -> None:
        for selector in self.NUMERIC_FORMS:
            with self.subTest(selector=selector):
                parsed = parse_selector(selector)
                self.assertIsNotNone(parsed)
                self.assertEqual(
                    "",
                    parsed.label,
                    f"{selector} must not be read as a label",
                )

    def test_mates_stay_opaque(self) -> None:
        # "m1" matches the label pattern; it must still be opaque so mate handling in the
        # consumers keeps working.
        for selector in ("m1", "m2", "M3"):
            with self.subTest(selector=selector):
                self.assertEqual("opaque", parse_selector(selector).selector_type)

    def test_entity_and_occurrence_forms_win_over_the_label_form(self) -> None:
        self.assertEqual("face", parse_selector("f45").selector_type)
        self.assertEqual("occurrence", parse_selector("o1.2").selector_type)
        self.assertEqual("shape", parse_selector("s7").selector_type)


if __name__ == "__main__":
    unittest.main()
