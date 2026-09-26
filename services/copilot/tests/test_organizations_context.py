"""Validating the `context` an Organizations send carries: the portfolio's own
filter rules, one canonical form, labels built on the server, and a focus
narrowed to the asker's filtered list."""

from django.test import SimpleTestCase

from services.copilot.dashboard_context import MAX_FOCUS_IDS
from services.copilot.organizations_context import (
    MAX_SEARCH_LENGTH,
    UNKNOWN,
    OrganizationsContextSerializer,
    clean_filters,
    filter_labels,
    params_of,
)
from services.organizations.params import parse_params

from .organizations_fixture import OrganizationsAskFixture

OPTIONS = {
    "owners": [{"value": "2", "name": "Carl CSM"}, {"value": "unassigned", "name": "Unassigned"}],
    "lifecycles": [],
    "products": [{"value": "9", "name": "Core"}],
}


class CleanFiltersTests(SimpleTestCase):
    def test_keeps_the_portfolio_filters_in_one_canonical_form(self):
        self.assertEqual(
            clean_filters(
                {
                    "search": " pizza ",
                    "owner": 2,
                    "lifecycle": ["live", "renewal"],
                    "health": "poor,good",
                    "product": "9",
                    "renews_within": 90,
                    "nps": "detractor",
                    "ids": "4,5",
                    "include_churned": "1",
                    "sort": "-risk",
                    "group": "owner",
                }
            ),
            {
                "search": "pizza",
                "owner": "2",
                "lifecycle": "live,renewal",
                "health": "poor,good",
                "product": "9",
                "renews_within": "90",
                "nps": "detractor",
                "ids": "4,5",
                "include_churned": "1",
                "sort": "-risk",
                "group": "owner",
            },
        )

    def test_paging_and_unknown_keys_are_dropped(self):
        self.assertEqual(
            clean_filters(
                {
                    "cursor": "abc",
                    "limit": "5",
                    "group_value": "live",
                    "horizon_days": "90",
                    "customer": "7",
                }
            ),
            {},
        )

    def test_unknown_values_are_dropped_not_rejected(self):
        self.assertEqual(
            clean_filters(
                {
                    "lifecycle": "bogus,live",
                    "health": "meh",
                    "renews_within": "45",
                    "nps": "fan",
                    "sort": "wat",
                    "group": "colour",
                    "include_churned": "yes",
                    "owner": "someone",
                }
            ),
            {"lifecycle": "live"},
        )

    def test_a_value_that_is_not_text_a_number_or_a_list_of_them_is_ignored(self):
        self.assertEqual(
            clean_filters(
                {"owner": True, "health": {"a": 1}, "product": [1, None], "search": None}
            ),
            {},
        )

    def test_overlong_values_are_ignored(self):
        self.assertEqual(clean_filters({"search": "x" * (MAX_SEARCH_LENGTH + 1)}), {})
        self.assertEqual(clean_filters({"product": ",".join(["1"] * 4000)}), {})

    def test_the_default_sort_is_not_stored(self):
        self.assertEqual(clean_filters({"sort": "-arr"}), {})

    def test_ids_sent_empty_name_nothing(self):
        self.assertEqual(clean_filters({"ids": ""}), {"ids": ""})
        self.assertEqual(params_of({"ids": ""}).ids, ())

    def test_the_canonical_form_parses_back_to_the_same_params(self):
        raw = {
            "search": " pizza ",
            "owner": "unassigned",
            "lifecycle": "live",
            "health": "poor",
            "product": "9,10",
            "renews_within": "30",
            "nps": "promoter",
            "ids": "4,5",
            "include_churned": "1",
            "sort": "name",
            "group": "renewal",
        }
        self.assertEqual(params_of(clean_filters(raw)), parse_params(raw))


class ParamsOfTests(SimpleTestCase):
    def test_a_view_opens_on_its_default_group_when_none_is_named(self):
        self.assertEqual(params_of({}, view="list").group, "health")
        self.assertEqual(params_of({}, view="board").group, "lifecycle")
        self.assertEqual(params_of({"group": "owner"}, view="board").group, "owner")
        self.assertEqual(params_of({}).group, "")

    def test_an_explicit_empty_group_means_not_grouped(self):
        self.assertEqual(clean_filters({"group": ""}), {"group": ""})
        self.assertEqual(params_of({"group": ""}, view="list").group, "")


class FilterLabelsTests(SimpleTestCase):
    def labels(self, **filters):
        return filter_labels(parse_params(filters), OPTIONS)

    def test_nothing_filtered_is_no_labels(self):
        self.assertEqual(self.labels(), [])

    def test_each_filter_is_named_from_the_askers_options(self):
        self.assertEqual(
            self.labels(
                ids="4,5",
                search="pizza",
                owner="2",
                lifecycle="live,renewal",
                health="poor",
                product="9",
                renews_within="90",
                nps="detractor",
                include_churned="1",
            ),
            [
                "Opened from the dashboard (2)",
                'Search: "pizza"',
                "Owner: Carl CSM",
                "Lifecycle: Live, Renewal",
                "Health: Poor",
                "Product: Core",
                "Renews within 90 days",
                "NPS: Detractors",
                "Includes churned",
            ],
        )

    def test_unassigned_is_named_even_without_an_option(self):
        self.assertEqual(
            filter_labels(parse_params({"owner": "unassigned"}), {**OPTIONS, "owners": []}),
            ["Owner: Unassigned"],
        )

    def test_an_owner_or_product_outside_the_options_is_never_named(self):
        self.assertEqual(
            self.labels(owner="77", product="9,88"),
            [f"Owner: {UNKNOWN}", f"Product: Core, {UNKNOWN}"],
        )

    def test_sort_and_group_are_not_filters(self):
        self.assertEqual(self.labels(sort="-risk", group="owner"), [])


class OrganizationsContextSerializerTests(OrganizationsAskFixture):
    def setUp(self):
        super().setUp()
        self.book()

    def check(self, data, user=None):
        serializer = OrganizationsContextSerializer(data=data, context={"user": user or self.csm})
        valid = serializer.is_valid()
        return valid, serializer.validated_data if valid else serializer.errors

    def test_a_valid_context_is_canonical_and_labelled_on_the_server(self):
        valid, data = self.check(self.context(owner=self.csm.pk, health="poor", cursor="x"))

        self.assertTrue(valid, data)
        self.assertEqual(
            data,
            {
                "surface": "organizations",
                "view": "list",
                "filters": {"owner": str(self.csm.pk), "health": "poor"},
                "labels": ["Owner: Carl CSM", "Health: Poor"],
                "focus": None,
            },
        )

    def test_labels_sent_by_the_client_are_ignored(self):
        valid, data = self.check({**self.context(), "labels": ["Owner: Dana CSM"]})

        self.assertTrue(valid, data)
        self.assertEqual(data["labels"], [])

    def test_another_csm_is_not_named_even_when_filtered_on(self):
        valid, data = self.check(self.context(owner=str(self.other.pk)))

        self.assertTrue(valid, data)
        self.assertEqual(data["labels"], [f"Owner: {UNKNOWN}"])

    def test_view_is_required_and_closed(self):
        cases = (
            ({**self.context(), "view": "grid"}, {"view": ['"grid" is not a valid choice.']}),
            ({"surface": "organizations", "filters": {}}, {"view": ["This field is required."]}),
        )
        for data, errors in cases:
            with self.subTest(data=data):
                valid, got = self.check(data)
                self.assertFalse(valid)
                self.assertEqual(got, errors)

    def test_only_a_companies_focus(self):
        valid, errors = self.check(self.context(focus={"kind": "attention", "key": "risk:1"}))

        self.assertFalse(valid)
        self.assertEqual(errors, {"focus": {"kind": ["Must be companies."]}})

    def test_focus_ids_are_checked_like_the_dashboards(self):
        cases = (
            ("1,2", "A list of company ids."),
            ([True], "A list of company ids."),
            (list(range(1, MAX_FOCUS_IDS + 2)), f"At most {MAX_FOCUS_IDS} companies."),
        )
        for ids, message in cases:
            with self.subTest(ids=ids):
                valid, errors = self.check(self.context(focus={"kind": "companies", "ids": ids}))
                self.assertFalse(valid)
                self.assertEqual(errors, {"focus": {"ids": [message]}})

    def test_focus_is_narrowed_to_the_askers_filtered_list_silently(self):
        focus = {"kind": "companies", "ids": [self.danas.pk, self.pizza.pk, self.hooli.pk]}

        valid, data = self.check(self.context(focus=focus, health="poor"))

        self.assertTrue(valid, data)
        self.assertEqual(data["focus"], {"kind": "companies", "ids": [self.hooli.pk]})
