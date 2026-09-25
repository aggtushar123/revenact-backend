from django.http import QueryDict
from django.test import SimpleTestCase

from services.organizations.params import (
    DEFAULT_LIMIT,
    DEFAULT_SORT,
    MAX_LIMIT,
    PortfolioParams,
    parse_params,
)


class ParseParamsTests(SimpleTestCase):
    def test_nothing_given_is_the_defaults(self):
        self.assertEqual(parse_params({}), PortfolioParams())
        params = parse_params({})
        self.assertEqual(params.sort, "-arr")
        self.assertEqual(params.limit, 50)
        self.assertIsNone(params.ids)

    def test_a_query_dict_works_like_a_dict(self):
        query = QueryDict("lifecycle=live,renewal&health=poor&owner=unassigned")
        params = parse_params(query)
        self.assertEqual(params.lifecycles, ("live", "renewal"))
        self.assertEqual(params.health, ("poor",))
        self.assertEqual(params.owner, "unassigned")

    def test_owner_is_an_id_or_unassigned(self):
        self.assertEqual(parse_params({"owner": "7"}).owner, 7)
        self.assertEqual(parse_params({"owner": "unassigned"}).owner, "unassigned")
        self.assertIsNone(parse_params({"owner": "carl"}).owner)

    def test_lists_drop_what_they_do_not_know(self):
        params = parse_params(
            {"lifecycle": "live, bogus,,churn", "health": "good,purple", "product": "3,x,4"}
        )
        self.assertEqual(params.lifecycles, ("live", "churn"))
        self.assertEqual(params.health, ("good",))
        self.assertEqual(params.products, (3, 4))

    def test_renews_within_is_one_of_three_windows(self):
        self.assertEqual(parse_params({"renews_within": "90"}).renews_within, 90)
        self.assertIsNone(parse_params({"renews_within": "45"}).renews_within)
        self.assertIsNone(parse_params({"renews_within": "soon"}).renews_within)

    def test_nps_is_one_band(self):
        self.assertEqual(parse_params({"nps": "detractor"}).nps, "detractor")
        self.assertIsNone(parse_params({"nps": "happy"}).nps)

    def test_ids_present_but_unusable_names_nothing(self):
        self.assertEqual(parse_params({"ids": ""}).ids, ())
        self.assertEqual(parse_params({"ids": "x,y"}).ids, ())
        self.assertEqual(parse_params({"ids": "5,zz,6"}).ids, (5, 6))

    def test_at_most_five_hundred_ids_are_read(self):
        raw = ",".join(["1"] * 500 + ["2"])
        self.assertNotIn(2, parse_params({"ids": raw}).ids)

    def test_include_churned_is_only_the_literal_one(self):
        self.assertTrue(parse_params({"include_churned": "1"}).include_churned)
        self.assertFalse(parse_params({"include_churned": "true"}).include_churned)

    def test_sort_accepts_known_keys_with_an_optional_minus(self):
        self.assertEqual(parse_params({"sort": "risk"}).sort, "risk")
        self.assertEqual(parse_params({"sort": "-total_hires"}).sort, "-total_hires")
        self.assertEqual(parse_params({"sort": "--arr"}).sort, DEFAULT_SORT)
        self.assertEqual(parse_params({"sort": "email"}).sort, DEFAULT_SORT)
        params = parse_params({"sort": "-touch"})
        self.assertEqual((params.sort_key, params.descending), ("touch", True))
        params = parse_params({"sort": "name"})
        self.assertEqual((params.sort_key, params.descending), ("name", False))

    def test_group_value_only_counts_with_a_group(self):
        self.assertEqual(parse_params({"group": "owner"}).group, "owner")
        self.assertEqual(parse_params({"group": "colour"}).group, "")
        self.assertIsNone(parse_params({"group_value": "live"}).group_value)
        params = parse_params({"group": "lifecycle", "group_value": "live"})
        self.assertEqual(params.group_value, "live")

    def test_limit_defaults_and_is_capped(self):
        self.assertEqual(parse_params({"limit": "20"}).limit, 20)
        self.assertEqual(parse_params({"limit": "1000"}).limit, MAX_LIMIT)
        self.assertEqual(parse_params({"limit": "0"}).limit, DEFAULT_LIMIT)
        self.assertEqual(parse_params({"limit": "lots"}).limit, DEFAULT_LIMIT)

    def test_search_is_trimmed(self):
        self.assertEqual(parse_params({"search": "  pizza "}).search, "pizza")
