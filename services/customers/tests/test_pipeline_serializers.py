"""The pipeline serializers on every existing endpoint: the new dates and the
Closed Lost stage are read and written, and `companies` names only the
organisations the caller may open (the twice-filter, closing "Known
consequences #2" for opportunities and risks)."""

from datetime import timedelta

from django.test import SimpleTestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.customers.models import Account, Customer, Opportunity, Risk
from services.customers.serializers import OpportunitySerializer


class Fixture(APITestCase):
    """Carl owns Pizza Hut. Dana owns Taco Bell and the account Shared, which
    is linked to both: Carl may open Shared (it sits under his organisation)
    but not Taco Bell, so Taco Bell must never be named to him."""

    def setUp(self):
        self.today = timezone.localdate()
        self.org = Organisation.objects.create(name="Acme Inc")
        self.csm = User.objects.create_user(
            email="carl@acme.io",
            password="supersecret1",
            name="Carl",
            organisation=self.org,
            role=User.Role.CSM,
        )
        self.other = User.objects.create_user(
            email="dana@acme.io",
            password="supersecret1",
            name="Dana",
            organisation=self.org,
            role=User.Role.CSM,
        )
        self.pizza = Customer.objects.create(
            organisation=self.org, name="Pizza Hut", owner=self.csm
        )
        self.taco = Customer.objects.create(
            organisation=self.org, name="Taco Bell", owner=self.other
        )
        self.shared = Account.objects.create(name="Shared", owner=self.other)
        self.shared.customers.add(self.pizza, self.taco)
        self.client.force_authenticate(self.csm)


class NewFieldsTests(Fixture):
    def test_an_opportunity_takes_an_expected_close_and_a_risk_a_due_by(self):
        close = (self.today + timedelta(days=30)).isoformat()
        response = self.client.post(
            "/api/v1/opportunities/",
            {
                "customer_id": self.pizza.pk,
                "title": "Upsell",
                "mrr": "100",
                "expected_close": close,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["expected_close"], close)
        self.assertIsNotNone(response.data["stage_changed_at"])
        response = self.client.post(
            f"/api/v1/customers/{self.pizza.pk}/risks/",
            {"title": "Budget", "mrr": "50", "due_by": close},
            format="json",
        )
        self.assertEqual((response.status_code, response.data["due_by"]), (201, close))

    def test_a_date_clears_with_null_and_rejects_nonsense(self):
        opportunity = Opportunity.objects.create(
            customer=self.pizza, title="Upsell", expected_close=self.today
        )
        url = f"/api/v1/opportunities/{opportunity.pk}/"
        response = self.client.patch(url, {"expected_close": None}, format="json")
        self.assertEqual((response.status_code, response.data["expected_close"]), (200, None))
        self.assertEqual(
            self.client.patch(url, {"expected_close": "soon"}, format="json").status_code, 400
        )

    def test_closed_lost_is_accepted_and_a_drag_moves_the_clock(self):
        opportunity = Opportunity.objects.create(customer=self.pizza, title="Upsell")
        long_ago = timezone.now() - timedelta(days=200)
        Opportunity.objects.filter(pk=opportunity.pk).update(stage_changed_at=long_ago)
        response = self.client.patch(
            f"/api/v1/opportunities/{opportunity.pk}/", {"stage": "closed_lost"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            (response.data["stage"], response.data["stage_display"]),
            ("closed_lost", "Closed Lost"),
        )
        opportunity.refresh_from_db()
        self.assertGreater(opportunity.stage_changed_at, long_ago)

    def test_the_clock_is_read_only(self):
        opportunity = Opportunity.objects.create(customer=self.pizza, title="Upsell")
        before = opportunity.stage_changed_at
        response = self.client.patch(
            f"/api/v1/opportunities/{opportunity.pk}/",
            {"stage_changed_at": "2020-01-01T00:00:00Z"},
            format="json",
        )
        # Accepted (the field is read-only, so it's ignored, not an error) ...
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNotNone(response.data["stage_changed_at"])
        self.assertFalse(response.data["stage_changed_at"].startswith("2020-01-01"))
        # ... and the clock did not move.
        opportunity.refresh_from_db()
        self.assertEqual(opportunity.stage_changed_at, before)


class CompaniesTrimTests(Fixture):
    """Every endpoint that serves an opportunity or a risk names only the
    organisations the caller may open."""

    def companies_everywhere(self, model, plural):
        item = model.objects.create(account=self.shared, title="On shared", mrr=10)
        seen = []
        for url in (
            f"/api/v1/{plural}/",
            f"/api/v1/customers/{self.pizza.pk}/{plural}/",
            f"/api/v1/customers/{self.pizza.pk}/accounts/{self.shared.pk}/{plural}/",
            f"/api/v1/accounts/{self.shared.pk}/{plural}/",
        ):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, (url, response.data))
            seen.append(next(r for r in response.data if r["id"] == item.pk)["companies"])
        seen.append(self.client.get(f"/api/v1/{plural}/{item.pk}/").data["companies"])
        response = self.client.patch(
            f"/api/v1/{plural}/{item.pk}/", {"title": "Renamed"}, format="json"
        )
        seen.append(response.data["companies"])
        response = self.client.post(
            f"/api/v1/{plural}/",
            {"account_id": self.shared.pk, "title": "New", "mrr": "5"},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        seen.append(response.data["companies"])
        return seen

    def test_opportunities_never_name_an_organisation_the_caller_cannot_open(self):
        for companies in self.companies_everywhere(Opportunity, "opportunities"):
            self.assertEqual(companies, [{"id": self.pizza.pk, "name": "Pizza Hut"}])

    def test_risks_never_name_one_either(self):
        for companies in self.companies_everywhere(Risk, "risks"):
            self.assertEqual(companies, [{"id": self.pizza.pk, "name": "Pizza Hut"}])

    def test_someone_who_may_open_both_sees_both(self):
        # Dana owns Taco Bell and the account under Pizza Hut, so she opens both.
        item = Opportunity.objects.create(account=self.shared, title="On shared", mrr=10)
        self.client.force_authenticate(self.other)
        companies = self.client.get(f"/api/v1/opportunities/{item.pk}/").data["companies"]
        self.assertCountEqual(
            companies,
            [{"id": self.pizza.pk, "name": "Pizza Hut"}, {"id": self.taco.pk, "name": "Taco Bell"}],
        )

    def test_companies_are_ordered_by_id(self):
        # Linked newest-first, so the link order and the id order disagree.
        zed = Customer.objects.create(organisation=self.org, name="Zed", owner=self.other)
        account = Account.objects.create(name="Backwards", owner=self.other)
        account.customers.add(zed)
        account.customers.add(self.taco)
        item = Risk.objects.create(account=account, title="Ordered", mrr=1)
        self.client.force_authenticate(self.other)
        for url in (f"/api/v1/risks/{item.pk}/", "/api/v1/risks/"):
            data = self.client.get(url).data
            row = data if isinstance(data, dict) else next(r for r in data if r["id"] == item.pk)
            self.assertEqual([c["id"] for c in row["companies"]], [self.taco.pk, zed.pk])

    def test_with_no_request_nothing_is_named(self):
        item = Opportunity.objects.create(account=self.shared, title="On shared", mrr=10)
        self.assertEqual(OpportunitySerializer(item).data["companies"], [])


class DepartmentLabelTests(SimpleTestCase):
    def test_a_department_reads_as_its_label_and_blank_or_unknown_as_empty(self):
        from services.customers.serializers import department_label

        self.assertEqual(department_label(User.Function.LEADERSHIP), "Leadership")
        self.assertEqual(department_label(""), "")
        self.assertEqual(department_label("nonsense"), "")
