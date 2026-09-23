"""The management brief, delivered on a schedule to Slack."""

import json
from datetime import date
from unittest.mock import patch

from django.utils import timezone
from rest_framework.test import APITestCase

from core.models import AuditEvent
from services.accounts.models import Organisation, User
from services.metrics.models import Brief, BriefSchedule

URL = "/api/v1/metrics/brief/schedule/"
POST_TO_SLACK = "services.metrics.delivery._post"
HOOK = "https://hooks.slack.com/services/T000/B000/xxxxxxxxxxxx"


class Fixture(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.alice = User.objects.create_user(
            email="alice@acme.io",
            password="x",
            name="Alice",
            organisation=self.org,
            role=User.Role.ADMIN,
        )
        self.dana = User.objects.create_user(
            email="dana@acme.io",
            password="x",
            name="Dana",
            organisation=self.org,
            role=User.Role.CSM,
        )
        self.client.force_authenticate(self.alice)

    def brief(self, as_of=None):
        return Brief.objects.create(
            organisation=self.org,
            as_of=as_of or timezone.localdate(),
            baseline=date(2026, 8, 31),
            headline="Net revenue retention slipped to 98%.",
            body="Two accounts downgraded.\n\nThe rest held.",
            watch=["Pizza Hut renewal", "Support backlog"],
            evidence={},
            generated_at=timezone.now(),
        )


class Scheduling(Fixture):
    def test_an_admin_sets_a_weekly_schedule_and_the_url_never_comes_back(self):
        response = self.client.post(
            URL, {"destination": HOOK, "cadence": "weekly", "weekday": 1}, format="json"
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertNotIn(HOOK, json.dumps(response.data))
        self.assertTrue(response.data["destination_hint"].endswith("xxxx"))
        self.assertEqual(response.data["cadence"], "weekly")
        self.assertTrue(AuditEvent.objects.filter(action="brief.schedule").exists())
        schedule = BriefSchedule.objects.get()
        self.assertEqual(schedule.destination, HOOK)

    def test_only_a_slack_hook_over_https_is_accepted(self):
        for bad in ["http://hooks.slack.com/services/x", "https://example.com/hook", "not-a-url"]:
            response = self.client.post(
                URL, {"destination": bad, "cadence": "weekly"}, format="json"
            )
            self.assertEqual(response.status_code, 400, bad)
        self.assertFalse(BriefSchedule.objects.exists())

    def test_reading_updating_and_removing_it(self):
        self.client.post(URL, {"destination": HOOK, "cadence": "weekly"}, format="json")
        self.assertEqual(self.client.get(URL).data["cadence"], "weekly")
        response = self.client.patch(URL, {"cadence": "monthly", "day": 1}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["cadence"], "monthly")
        self.assertEqual(self.client.delete(URL).status_code, 204)
        self.assertEqual(self.client.get(URL).data["cadence"], None)

    def test_a_csm_can_neither_see_nor_set_it(self):
        self.client.post(URL, {"destination": HOOK, "cadence": "weekly"}, format="json")
        self.client.force_authenticate(self.dana)
        self.assertEqual(self.client.get(URL).status_code, 403)
        self.assertEqual(
            self.client.post(
                URL, {"destination": HOOK, "cadence": "weekly"}, format="json"
            ).status_code,
            403,
        )

    def test_another_tenant_has_its_own(self):
        self.client.post(URL, {"destination": HOOK, "cadence": "weekly"}, format="json")
        other = Organisation.objects.create(name="Other")
        outsider = User.objects.create_user(
            email="o@other.io", password="x", name="O", organisation=other, role=User.Role.ADMIN
        )
        self.client.force_authenticate(outsider)
        self.assertIsNone(self.client.get(URL).data["cadence"])


class Sending(Fixture):
    def setUp(self):
        super().setUp()
        self.schedule = BriefSchedule.objects.create(
            organisation=self.org, destination=HOOK, cadence=BriefSchedule.Cadence.WEEKLY
        )

    @patch(POST_TO_SLACK, return_value=(True, 200, ""))
    def test_send_now_posts_the_latest_brief(self, post):
        self.brief()
        response = self.client.post(f"{URL}send/", {}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["sent"])
        url, payload = post.call_args[0]
        self.assertEqual(url, HOOK)
        self.assertIn("Net revenue retention slipped", payload["text"])
        self.assertIn("Pizza Hut renewal", payload["text"])
        self.schedule.refresh_from_db()
        self.assertIsNotNone(self.schedule.last_sent_at)
        self.assertTrue(AuditEvent.objects.filter(action="brief.sent").exists())

    @patch(POST_TO_SLACK, return_value=(True, 200, ""))
    def test_with_no_brief_it_says_so_and_posts_nothing(self, post):
        response = self.client.post(f"{URL}send/", {}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data["sent"])
        post.assert_not_called()

    @patch(POST_TO_SLACK, return_value=(False, 500, "channel_not_found"))
    def test_a_refused_post_is_reported_and_not_recorded_as_sent(self, post):
        self.brief()
        response = self.client.post(f"{URL}send/", {}, format="json")
        self.assertEqual(response.status_code, 502, response.data)
        self.schedule.refresh_from_db()
        self.assertIsNone(self.schedule.last_sent_at)

    def test_sending_without_a_schedule_is_404(self):
        self.schedule.delete()
        self.assertEqual(self.client.post(f"{URL}send/", {}, format="json").status_code, 404)


class TheScheduledPass(Fixture):
    def setUp(self):
        super().setUp()
        self.schedule = BriefSchedule.objects.create(
            organisation=self.org,
            destination=HOOK,
            cadence=BriefSchedule.Cadence.WEEKLY,
            weekday=0,
        )

    def monday(self, hour=0):
        """The nightly job runs a little after midnight, so that is when
        the pass is asked — not at some hour a schedule chose."""
        return timezone.make_aware(timezone.datetime(2026, 9, 21, hour, 5))

    @patch(POST_TO_SLACK, return_value=(True, 200, ""))
    def test_sends_on_its_day_and_hour_and_not_twice(self, post):
        from services.metrics.delivery import send_due

        self.brief()
        self.assertEqual(send_due(now=self.monday()), 1)
        self.assertEqual(post.call_count, 1)
        # Same day, later hour: already sent.
        self.assertEqual(send_due(now=self.monday(hour=23)), 0)

    @patch(POST_TO_SLACK, return_value=(True, 200, ""))
    def test_waits_for_its_day(self, post):
        from services.metrics.delivery import send_due

        self.brief()
        tuesday = timezone.make_aware(timezone.datetime(2026, 9, 22, 0, 5))
        self.assertEqual(send_due(now=tuesday), 0)
        post.assert_not_called()

    @patch(POST_TO_SLACK, return_value=(True, 200, ""))
    def test_the_nightly_job_is_what_actually_posts_it(self, post):
        """The pass as the cron entry really calls it: no injected time,
        no chosen hour. A schedule that only fires for a caller who picks
        the right moment is a schedule that never fires."""
        from django.core.management import call_command

        self.brief()
        self.schedule.weekday = timezone.localtime(timezone.now()).weekday()
        self.schedule.save(update_fields=["weekday"])
        call_command("run_health_maintenance", "--org-email", "alice@acme.io")
        self.schedule.refresh_from_db()
        self.assertIsNotNone(self.schedule.last_sent_at)
        self.assertEqual(post.call_count, 1)

    @patch(POST_TO_SLACK, return_value=(True, 200, ""))
    def test_a_monthly_schedule_waits_for_its_day_of_the_month(self, post):
        from services.metrics.delivery import send_due

        self.brief()
        self.schedule.cadence = BriefSchedule.Cadence.MONTHLY
        self.schedule.day = 21
        self.schedule.save(update_fields=["cadence", "day"])
        self.assertEqual(send_due(now=self.monday()), 1)
        self.schedule.last_sent_at = None
        self.schedule.day = 22
        self.schedule.save(update_fields=["last_sent_at", "day"])
        self.assertEqual(send_due(now=self.monday()), 0)

    @patch(POST_TO_SLACK, return_value=(True, 200, ""))
    def test_an_inactive_schedule_and_one_with_no_brief_send_nothing(self, post):
        from services.metrics.delivery import send_due

        self.schedule.is_active = False
        self.schedule.save(update_fields=["is_active"])
        self.assertEqual(send_due(now=self.monday()), 0)
        self.schedule.is_active = True
        self.schedule.save(update_fields=["is_active"])
        self.assertEqual(send_due(now=self.monday()), 0)  # no brief exists
        post.assert_not_called()

    @patch(POST_TO_SLACK, side_effect=RuntimeError("network down"))
    def test_one_organisation_failing_does_not_stop_the_others(self, post):
        from services.metrics.delivery import send_due

        self.brief()
        other = Organisation.objects.create(name="Other")
        BriefSchedule.objects.create(
            organisation=other, destination=HOOK, cadence=BriefSchedule.Cadence.WEEKLY, weekday=0
        )
        Brief.objects.create(
            organisation=other,
            as_of=timezone.localdate(),
            baseline=date(2026, 8, 31),
            headline="All steady.",
            body="Nothing moved.",
            watch=[],
            evidence={},
            generated_at=timezone.now(),
        )
        # Neither send succeeds, but both are attempted and nothing raises.
        self.assertEqual(send_due(now=self.monday()), 0)
        self.assertEqual(post.call_count, 2)
