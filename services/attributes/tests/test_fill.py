"""Unit tier: the model's answer → a typed value, no DB, no HTTP."""

from django.test import SimpleTestCase

from services.attributes.fill import coerce, parse_answer


class ParseAnswer(SimpleTestCase):
    def test_reads_plain_json(self):
        self.assertEqual(
            parse_answer(
                '{"value": "Enterprise", "reasoning": "Three notes say so", "evidence": [0, 2]}'
            ),
            {"value": "Enterprise", "reasoning": "Three notes say so", "evidence": [0, 2]},
        )

    def test_strips_a_code_fence(self):
        self.assertEqual(
            parse_answer('```json\n{"value": 3, "reasoning": "r", "evidence": []}\n```')["value"], 3
        )

    def test_rejects_non_json_and_non_objects(self):
        with self.assertRaises(ValueError):
            parse_answer("I think it is Enterprise.")
        with self.assertRaises(ValueError):
            parse_answer("[1, 2]")


class Coerce(SimpleTestCase):
    def test_text_is_stripped(self):
        self.assertEqual(coerce("text", "  Seat licences  "), "Seat licences")

    def test_number_accepts_numbers_and_numeric_strings(self):
        self.assertEqual(coerce("number", 42), 42)
        self.assertEqual(coerce("number", "12.5"), 12.5)
        self.assertEqual(coerce("number", "$1,200"), 1200)
        with self.assertRaises(ValueError):
            coerce("number", "twelve")

    def test_boolean_reads_yes_no_and_bools(self):
        self.assertIs(coerce("boolean", True), True)
        self.assertIs(coerce("boolean", "yes"), True)
        self.assertIs(coerce("boolean", "No"), False)
        with self.assertRaises(ValueError):
            coerce("boolean", "maybe")

    def test_picklist_matches_an_option_ignoring_case(self):
        self.assertEqual(coerce("picklist", "enterprise", ["SMB", "Enterprise"]), "Enterprise")
        with self.assertRaises(ValueError):
            coerce("picklist", "Mid-market", ["SMB", "Enterprise"])

    def test_none_stays_none(self):
        self.assertIsNone(coerce("number", None))
        self.assertIsNone(coerce("text", ""))
