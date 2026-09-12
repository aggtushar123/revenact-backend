"""Initiatives: decisions judged against the metric layer."""

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.customers.models import Customer, Product, Task
from services.metrics.models import Initiative


class InitiativeAPITests(APITestCase):
    url = "/api/v1/metrics/initiatives/"

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc", currency="USD")
        self.admin = User.objects.create_user(
            email="alice@acme.io",
            password="x",
            name="Alice",
            organisation=self.org,
            role=User.Role.ADMIN,
        )
        self.csm = User.objects.create_user(
            email="carl@acme.io",
            password="x",
            name="Carl",
            organisation=self.org,
            role=User.Role.CSM,
        )
        self.b = Product.objects.create(organisation=self.org, name="Product B")
        self.shaky = Customer.objects.create(
            organisation=self.org,
            name="Shaky",
            owner=self.csm,
            primary_product=self.b,
            arr_billed_at_account=Decimal(100_000),
            health_score=Decimal("2.0"),
            renewal_date=timezone.localdate() + timedelta(days=30),
        )
        Customer.objects.create(
            organisation=self.org,
            name="Fine",
            owner=self.csm,
            primary_product=self.b,
            arr_billed_at_account=Decimal(200_000),
            health_score=Decimal("8.5"),
        )
        self.today = timezone.localdate()
        self.client.force_authenticate(self.admin)

    def _post(self, **overrides):
        body = {
            "title": "Bring Product B's risk down",
            "hypothesis": "If we run a save play on the shaky account, then ARR at risk on "
            "Product B halves.",
            "metric": "at_risk_arr",
            "dimension": "product",
            "member": str(self.b.pk),
            "target_value": "30000",
            "target_by": (self.today + timedelta(days=60)).isoformat(),
            "owner_id": self.csm.pk,
        }
        body.update(overrides)
        return self.client.post(self.url, body, format="json")

    def test_a_csm_is_refused(self):
        self.client.force_authenticate(self.csm)
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_403_FORBIDDEN)

    def test_creating_captures_the_starting_line_from_the_registry(self):
        response = self._post()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        data = response.data
        self.assertEqual(data["metric_label"], "ARR at risk")
        self.assertEqual(data["dimension_label"], "Product")
        self.assertEqual(data["member_label"], "Product B")
        self.assertEqual(data["baseline_as_of"], self.today.isoformat())
        # The shaky account carries risk, so the baseline is a real number
        # and progress toward the target is measurable from day one.
        self.assertGreater(float(data["baseline_value"]), 0)
        self.assertEqual(data["progress"]["current"], float(data["baseline_value"]))
        self.assertEqual(data["progress"]["progress_pct"], 0.0)
        self.assertEqual(data["progress"]["days_left"], 60)
        self.assertEqual(data["progress"]["direction"], "down")
        self.assertEqual(data["owner"], {"id": self.csm.pk, "name": "Carl"})

    def test_progress_moves_as_the_registry_moves(self):
        created = self._post().data
        # The save play worked: the account is healthy now, so it carries no
        # churn risk and the number falls toward the target.
        Customer.objects.filter(pk=self.shaky.pk).update(health_score=Decimal("9.0"))

        row = self.client.get(f"{self.url}{created['id']}/").data

        self.assertLess(row["progress"]["current"], created["progress"]["current"])
        self.assertGreater(row["progress"]["progress_pct"], 0.0)
        # The starting line does not move.
        self.assertEqual(row["baseline_value"], created["baseline_value"])

    def test_a_whole_org_initiative_needs_no_cut(self):
        response = self._post(dimension="", member="", metric="healthy_share", target_value="80")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["member_label"], "")
        self.assertEqual(response.data["progress"]["direction"], "up")

    def test_only_the_registrys_cuts_are_allowed(self):
        self.assertEqual(self._post(metric="made_up").status_code, status.HTTP_400_BAD_REQUEST)
        response = self._post(metric="active_arr", dimension="owner", member=str(self.csm.pk))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("lifecycle, segment", str(response.data["dimension"]))
        self.assertEqual(self._post(member="999999").status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self._post(dimension="").status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_target_date_in_the_past_is_refused(self):
        response = self._post(target_by=(self.today - timedelta(days=1)).isoformat())

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_an_owner_from_another_organisation_is_refused(self):
        other = Organisation.objects.create(name="Other Inc", currency="USD")
        stranger = User.objects.create_user(
            email="s@other.io", password="x", name="S", organisation=other, role=User.Role.CSM
        )

        self.assertEqual(self._post(owner_id=stranger.pk).status_code, status.HTTP_400_BAD_REQUEST)

    def test_an_initiative_shows_the_work_under_it_open_first(self):
        initiative = Initiative.objects.create(
            organisation=self.org,
            title="Halve it",
            metric="at_risk_arr",
            target_value=1,
            target_by=timezone.localdate() + timedelta(days=30),
            baseline_as_of=timezone.localdate(),
        )
        today = timezone.localdate()
        Task.objects.create(
            customer=self.shaky,
            title="Later",
            assignee_name="Carl",
            due_date=today + timedelta(days=9),
            priority="low",
            initiative=initiative,
        )
        Task.objects.create(
            customer=self.shaky,
            title="Soon",
            assignee_name="Carl",
            due_date=today + timedelta(days=2),
            priority="high",
            initiative=initiative,
        )
        Task.objects.create(
            customer=self.shaky,
            title="Finished",
            assignee_name="Carl",
            due_date=today,
            priority="high",
            status="completed",
            initiative=initiative,
        )
        Task.objects.create(
            customer=self.shaky,
            title="Unrelated",
            assignee_name="Carl",
            due_date=today,
            priority="low",
        )

        self.client.force_authenticate(self.admin)
        work = self.client.get(f"/api/v1/metrics/initiatives/{initiative.id}/").data["work"]

        self.assertEqual((work["open"], work["done"]), (2, 1))
        self.assertEqual([t["title"] for t in work["tasks"]], ["Soon", "Later", "Finished"])
        self.assertEqual(work["tasks"][0]["parent_name"], "Shaky")
        self.assertEqual(work["tasks"][0]["status"], "pending")

    def test_closing_stamps_the_time_and_reopening_clears_it(self):
        created = self._post().data

        done = self.client.patch(
            f"{self.url}{created['id']}/",
            {"status": "done", "outcome": "It worked."},
            format="json",
        ).data
        self.assertIsNotNone(done["closed_at"])
        self.assertEqual(done["outcome"], "It worked.")

        reopened = self.client.patch(
            f"{self.url}{created['id']}/", {"status": "active"}, format="json"
        ).data
        self.assertIsNone(reopened["closed_at"])

    def test_another_organisations_initiative_is_invisible(self):
        other = Organisation.objects.create(name="Other Inc", currency="USD")
        theirs = Initiative.objects.create(
            organisation=other,
            title="Theirs",
            metric="active_arr",
            target_value=1,
            target_by=self.today,
            baseline_as_of=self.today,
        )

        self.assertEqual(
            self.client.get(f"{self.url}{theirs.id}/").status_code, status.HTTP_404_NOT_FOUND
        )
        self.assertEqual([i["id"] for i in self.client.get(self.url).data], [])

    def test_a_list_of_initiatives_runs_the_rollups_once(self):
        """Twenty decisions on the same cut must not run the rollups twenty
        times: one Figures per request, read by every row."""
        from unittest.mock import patch

        for n in range(3):
            self._post(title=f"Initiative {n}")

        from services.metrics import initiatives as module

        with patch.object(module, "compute_slices", wraps=module.compute_slices) as slices:
            response = self.client.get(self.url)

        self.assertEqual(len(response.data), 3)
        self.assertEqual(slices.call_count, 1)
