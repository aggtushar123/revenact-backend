"""The keyword mapping that converts the free-text churn reasons.

Worth testing because it runs exactly once against a real book and then the
original wording is gone from the column. The migration keeps it importable so
this can check the two things that matter: what each phrase becomes, and that
nothing it can't place is thrown away.
"""

from importlib import import_module

from django.test import TestCase

from services.customers.models import Customer

# A migration module's name isn't an identifier, so it can't be imported with
# `from ... import`. The mapping lives in the migration on purpose — see that
# file's own docstring — and is imported rather than duplicated here.
mapping = import_module("services.customers.migrations.0028_map_churn_reasons_to_choices")

Reason = Customer.ChurnReason


class ChurnReasonMappingTests(TestCase):
    def test_the_spellings_that_motivated_the_change_all_land_together(self):
        for raw in ("Budget cuts", "  budget CUTS ", "Budget Cut", "budget cut"):
            self.assertEqual(mapping.classify(raw), (Reason.BUDGET, False), raw)

    def test_wordings_no_folding_could_ever_have_merged(self):
        # The argument for the closed list: these are one reason and no
        # normalisation of free text would have grouped them.
        self.assertEqual(mapping.classify("Price")[0], Reason.PRICE)
        self.assertEqual(mapping.classify("Too expensive")[0], Reason.PRICE)

    def test_each_reason_on_the_list_is_reachable_from_something_a_csm_types(self):
        phrases = {
            "Too expensive for the value": Reason.PRICE,
            "Budget was cut for next year": Reason.BUDGET,
            "Missing SSO, blocked their rollout": Reason.PRODUCT_GAP,
            "Never adopted past the pilot team": Reason.ADOPTION,
            "Switched to a competitor": Reason.COMPETITOR,
            "Our champion left the company": Reason.CHAMPION_LEFT,
            "Acquired by a larger group": Reason.ACQUIRED,
            "Went out of business": Reason.SHUT_DOWN,
            "Vendor consolidation programme": Reason.CONSOLIDATION,
            "Unhappy with support response times": Reason.SUPPORT,
        }
        for raw, expected in phrases.items():
            self.assertEqual(mapping.classify(raw), (expected, False), raw)

    def test_a_blank_reason_stays_blank_rather_than_becoming_other(self):
        # "Nobody recorded why" is a gap in the CRM. "Other" is a CSM saying
        # none of the eleven fit. Turning the first into the second would
        # invent a decision nobody made.
        self.assertEqual(mapping.classify(""), ("", False))
        self.assertEqual(mapping.classify("   "), ("", False))
        self.assertEqual(mapping.classify(None), ("", False))

    def test_something_nobody_can_classify_becomes_other_and_asks_to_be_kept(self):
        value, keep_wording = mapping.classify("Bulk test churn")

        self.assertEqual(value, Reason.OTHER)
        # The second item is what makes the migration copy the original
        # wording into churn_comment: an unclassifiable reason is still the
        # only record of why that customer left.
        self.assertTrue(keep_wording)

    def test_the_more_specific_phrase_wins_over_the_word_inside_it(self):
        # "cost cut" is a budget decision; "cost" alone is about price.
        self.assertEqual(mapping.classify("Cost cutting exercise")[0], Reason.BUDGET)
        self.assertEqual(mapping.classify("Cost per seat too high")[0], Reason.PRICE)
        # "Went out of business" contains neither, and must not be read as a
        # competitive loss because it mentions no competitor.
        self.assertEqual(mapping.classify("Company went out of business")[0], Reason.SHUT_DOWN)

    def test_every_value_it_can_produce_is_a_real_choice(self):
        produced = {value for _, value in mapping.KEYWORDS} | {Reason.OTHER}

        self.assertTrue(produced.issubset(set(Reason.values)))
