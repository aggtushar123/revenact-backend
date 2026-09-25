"""The platform is metadata-only: /admin/ must never surface the
model-written `title`/`summary` an anomaly carries — an audit trail of
who did what, not a place to read customer reports."""

from django.contrib import admin
from django.test import TestCase

from services.anomalies.models import Anomaly


class AnomalyAdminTests(TestCase):
    def setUp(self):
        self.model_admin = admin.site._registry[Anomaly]

    def test_list_display_never_shows_model_written_text(self):
        self.assertNotIn("title", self.model_admin.list_display)
        self.assertNotIn("summary", self.model_admin.list_display)

    def test_search_never_reads_model_written_text(self):
        self.assertNotIn("title", self.model_admin.search_fields)
        self.assertNotIn("summary", self.model_admin.search_fields)

    def test_list_display_shows_metadata_instead(self):
        self.assertIn("id", self.model_admin.list_display)
        self.assertIn("organisation", self.model_admin.list_display)
        self.assertIn("status", self.model_admin.list_display)
        self.assertIn("created_at", self.model_admin.list_display)

    def test_title_and_summary_are_kept_out_of_the_change_form(self):
        excluded = set(getattr(self.model_admin, "exclude", None) or ())
        readonly = set(self.model_admin.get_readonly_fields(request=None))
        self.assertTrue({"title", "summary"} <= (excluded | readonly))
