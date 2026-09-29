from django.apps import apps
from django.http import QueryDict
from django.test import SimpleTestCase

from services.accounts_portfolio.params import (
    GROUPS,
    SORT_KEYS,
    AccountPortfolioParams,
    parse_params,
)


class AppTests(SimpleTestCase):
    def test_the_app_is_installed(self):
        self.assertEqual(
            apps.get_app_config("accounts_portfolio").name, "services.accounts_portfolio"
        )


class ParseParamsTests(SimpleTestCase):
    def test_nothing_given_is_the_defaults(self):
        params = parse_params({})
        self.assertEqual(params, AccountPortfolioParams())
        self.assertEqual(
            (params.sort, params.limit, params.ids, params.group), ("-arr", 50, None, "")
        )
        self.assertEqual((params.sort_key, params.descending), ("arr", True))

    def test_the_five_sorts_and_four_groups(self):
        self.assertEqual(SORT_KEYS, ("risk", "arr", "renewal", "health", "name"))
        self.assertEqual(GROUPS, ("health", "lifecycle", "owner", "renewal"))
        self.assertEqual(parse_params({"sort": "-risk"}).sort, "-risk")
        self.assertEqual(parse_params({"sort": "name"}).sort, "name")
        # Organizations-only sorts and groups are unknown here, so dropped.
        for other in ("touch", "-nps_score", "arr_billed_at_hq"):
            self.assertEqual(parse_params({"sort": other}).sort, "-arr")
        self.assertEqual(parse_params({"group": "product"}).group, "")

    def test_a_query_dict_works_like_a_dict(self):
        params = parse_params(
            QueryDict(
                "organisation=3,x,4&lifecycle=live,churn,bogus&health=poor,purple&owner=unassigned"
            )
        )
        self.assertEqual(params.organisations, (3, 4))
        self.assertEqual(params.lifecycles, ("live", "churn"))
        self.assertEqual(params.health, ("poor",))
        self.assertEqual(params.owner, "unassigned")

    def test_owner_is_an_id_or_unassigned(self):
        self.assertEqual(parse_params({"owner": "7"}).owner, 7)
        self.assertEqual(parse_params({"owner": "unassigned"}).owner, "unassigned")
        self.assertIsNone(parse_params({"owner": "carl"}).owner)

    def test_windows_bands_and_limit(self):
        self.assertEqual(parse_params({"renews_within": "90"}).renews_within, 90)
        self.assertIsNone(parse_params({"renews_within": "45"}).renews_within)
        self.assertEqual(parse_params({"nps": "detractor"}).nps, "detractor")
        self.assertIsNone(parse_params({"nps": "fan"}).nps)
        self.assertEqual(parse_params({"limit": "500"}).limit, 100)
        self.assertEqual(parse_params({"limit": "0"}).limit, 50)
        self.assertEqual(parse_params({"limit": "x"}).limit, 50)
        self.assertEqual(parse_params({"limit": "20"}).limit, 20)

    def test_ids(self):
        self.assertIsNone(parse_params({}).ids)
        self.assertEqual(parse_params({"ids": "7,x,9"}).ids, (7, 9))
        self.assertEqual(parse_params({"ids": ""}).ids, ())
        many = ",".join(str(i) for i in range(1, 700))
        self.assertEqual(len(parse_params({"ids": many}).ids), 500)

    def test_group_value_needs_a_group(self):
        self.assertIsNone(parse_params({"group_value": "live"}).group_value)
        self.assertEqual(
            parse_params({"group": "lifecycle", "group_value": "live"}).group_value, "live"
        )

    def test_there_is_no_churned_switch(self):
        self.assertFalse(hasattr(parse_params({"include_churned": "1"}), "include_churned"))
