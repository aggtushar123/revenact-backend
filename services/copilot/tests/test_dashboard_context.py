"""Validating the `context` a dashboard send carries."""

from datetime import timedelta

from django.test import SimpleTestCase

from services.copilot.dashboard_context import (
    DASHBOARD_VIEWS,
    MAX_FOCUS_IDS,
    NOT_ON_LIST,
    DashboardContextSerializer,
    clean_filters,
    origin_of,
)

from .dashboard_fixture import POOR, DashboardFixture


class CatalogueTests(SimpleTestCase):
    def test_mirrors_the_frontend_areas(self):
        # react-ts-app/src/pages/dashboard/areas.ts, plus the Overview root.
        self.assertEqual(
            {area: list(views) for area, views in DASHBOARD_VIEWS.items()},
            {
                "overview": [],
                "revenue": ["forecast", "customers", "products"],
                "health": [
                    "triage",
                    "divergence",
                    "movement",
                    "renewals",
                    "usage",
                    "activity",
                    "distribution",
                ],
                "support": ["tickets", "topics"],
            },
        )


class CleanFiltersTests(SimpleTestCase):
    def test_keeps_the_three_shared_keys_and_drops_the_rest(self):
        self.assertEqual(
            clean_filters(
                {"owner": 2, "lifecycle": "live", "horizon_days": "90", "customer": None}
            ),
            {"owner": "2", "lifecycle": "live", "customer": ""},
        )

    def test_a_value_that_is_not_text_or_a_number_is_ignored(self):
        self.assertEqual(
            clean_filters({"owner": ["2"], "lifecycle": True, "customer": "x" * 65}),
            {"owner": "", "lifecycle": "", "customer": ""},
        )

    def test_origin_is_the_context_without_its_focus(self):
        context = {"surface": "dashboard", "area": "health", "view": "triage", "filters": {}}
        self.assertEqual(
            origin_of({**context, "focus": {"kind": "attention", "key": "risk:1"}}), context
        )


class DashboardContextSerializerTests(DashboardFixture):
    def validate(self, data, user=None):
        serializer = DashboardContextSerializer(data=data, context={"user": user or self.csm})
        valid = serializer.is_valid()
        return valid, (serializer.validated_data if valid else serializer.errors)

    def test_a_valid_context_is_normalised(self):
        valid, data = self.validate(
            {"surface": "dashboard", "area": "revenue", "view": "forecast", "filters": {"owner": 7}}
        )
        self.assertTrue(valid)
        self.assertEqual(
            data,
            {
                "surface": "dashboard",
                "area": "revenue",
                "view": "forecast",
                "filters": {"owner": "7", "lifecycle": "", "customer": ""},
                "focus": None,
            },
        )

    def test_field_errors(self):
        cases = {
            "surface": {**self.context(), "surface": "communications"},
            "area": {**self.context(), "area": "brain"},
            "view": self.context("overview", "forecast"),
        }
        for field, data in cases.items():
            with self.subTest(field=field):
                valid, errors = self.validate(data)
                self.assertFalse(valid)
                self.assertIn(field, errors)

    def test_a_view_must_belong_to_its_area(self):
        for area, view in (("revenue", None), ("revenue", "triage"), ("support", "")):
            with self.subTest(area=area, view=view):
                valid, errors = self.validate(self.context(area, view))
                self.assertFalse(valid)
                self.assertIn("view", errors)

    def test_focus_errors(self):
        cases = {
            "kind": {"kind": "segment"},
            "ids": {"kind": "companies", "ids": list(range(1, MAX_FOCUS_IDS + 2))},
        }
        for field, focus in cases.items():
            with self.subTest(field=field):
                valid, errors = self.validate(self.context(focus=focus))
                self.assertFalse(valid)
                self.assertIn(field, errors["focus"])

    def test_ids_must_be_whole_numbers(self):
        valid, errors = self.validate(self.context(focus={"kind": "companies", "ids": ["1"]}))
        self.assertFalse(valid)
        self.assertIn("ids", errors["focus"])

    def test_focus_ids_outside_the_filtered_book_are_dropped_silently(self):
        mine = self.customer("Mine", lifecycle_stage="live")
        onboarding = self.customer("Onboarding", lifecycle_stage="onboarding")
        theirs = self.customer("Theirs", owner=self.other)
        focus = {"kind": "companies", "ids": [theirs.pk, mine.pk, onboarding.pk, 999_999]}

        valid, data = self.validate(self.context(focus=focus, lifecycle="live"))

        self.assertTrue(valid)
        self.assertEqual(data["focus"], {"kind": "companies", "ids": [mine.pk]})

    def test_an_attention_key_on_the_viewers_list(self):
        soon = self.customer(
            "Soon", health_score=POOR, renewal_date=self.today + timedelta(days=10)
        )
        focus = {"kind": "attention", "key": f"renewal:{soon.pk}"}

        valid, data = self.validate(self.context(focus=focus))

        self.assertTrue(valid)
        self.assertEqual(data["focus"], focus)

    def test_a_key_not_on_the_list_reads_exactly_like_a_malformed_one(self):
        theirs = self.customer(
            "Theirs",
            owner=self.other,
            health_score=POOR,
            renewal_date=self.today + timedelta(days=10),
        )
        for key in (f"renewal:{theirs.pk}", "renewal:abc", 12, None):
            with self.subTest(key=key):
                valid, errors = self.validate(self.context(focus={"kind": "attention", "key": key}))
                self.assertFalse(valid)
                self.assertEqual(errors, {"focus": {"key": [NOT_ON_LIST]}})
