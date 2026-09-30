"""The account page's per-account routes, keyed by the account alone
(`/api/v1/accounts/<id>/…`): the nested views' own querysets, record rules
and create paths, reached without an organisation id."""

from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.customers.models import (
    Attachment,
    Call,
    Contact,
    Customer,
    Note,
    Opportunity,
    Risk,
    Survey,
    Task,
)
from services.customers.tests.test_views import blind_to_one_account, create_account

TABS = ("contacts", "opportunities", "risks", "files", "calls", "surveys", "tasks", "notes")

#: A valid JSON create body per tab (files are multipart, tested on their own).
BODIES = {
    "contacts": {"name": "Sam Buyer", "email": "sam@pizza.example"},
    "opportunities": {"title": "Upsell SSO"},
    "risks": {"title": "Champion left"},
    "calls": {"title": "QBR", "occurred_at": "2026-09-29T10:00:00Z"},
    "surveys": {"survey_type": "nps", "sent_at": "2026-09-01"},
    "tasks": {"title": "Send the deck", "due_date": "2026-10-01", "priority": "high"},
    "notes": {"title": "Kickoff", "body": "Went well."},
}
MODELS = {
    "contacts": Contact,
    "opportunities": Opportunity,
    "risks": Risk,
    "calls": Call,
    "surveys": Survey,
    "tasks": Task,
    "notes": Note,
}


def flat(account_id, tab):
    return f"/api/v1/accounts/{account_id}/{tab}/"


class AccountRouteFixture(APITestCase):
    """Acme's Pizza Hut, owned by a colleague; the viewer owns its Seen
    account and cannot open its Hidden one (`blind_to_one_account`). Globex
    is another tenant."""

    def setUp(self):
        from services.copilot.anthropic_client import CopilotNotConfigured

        no_model = patch(
            "services.customers.classification.get_completion",
            side_effect=CopilotNotConfigured("no model in tests"),
        )
        no_model.start()
        self.addCleanup(no_model.stop)
        self.today = timezone.localdate()
        self.org = Organisation.objects.create(name="Acme")
        self.admin = User.objects.create_user(
            email="alice@acme.io",
            password="x",
            name="Alice",
            organisation=self.org,
            role=User.Role.ADMIN,
        )
        self.pizza = Customer.objects.create(organisation=self.org, name="Pizza Hut")
        self.viewer, self.seen, self.hidden = blind_to_one_account(self.pizza)
        self.colleague = self.pizza.owner
        globex = Customer.objects.create(
            organisation=Organisation.objects.create(name="Globex"), name="Globex"
        )
        self.stranger = create_account(globex, name="Stranger")

    def fill(self, account):
        """One record of every tab's kind on `account`."""
        Contact.objects.create(account=account, name="Sam Buyer", email="sam@pizza.example")
        Opportunity.objects.create(account=account, title="Upsell SSO")
        Risk.objects.create(account=account, title="Champion left")
        Attachment.objects.create(
            organisation=self.org,
            account=account,
            file=SimpleUploadedFile("a.txt", b"hello", content_type="text/plain"),
            name="a.txt",
            content_type="text/plain",
            size=5,
        )
        Call.objects.create(
            account=account, title="QBR", host_name="Viewer", occurred_at=timezone.now()
        )
        Survey.objects.create(
            account=account, survey_type=Survey.SurveyType.NPS, sent_at=self.today
        )
        Task.objects.create(
            account=account,
            title="Send the deck",
            assignee_name="Viewer",
            due_date=self.today,
            priority=Task.Priority.HIGH,
        )
        Note.objects.create(
            account=account,
            title="Kickoff",
            author_name="Viewer",
            body="Went well.",
            logged_at=self.today,
        )

    def get(self, user, path):
        self.client.force_authenticate(user)
        return self.client.get(path)


class AccountRouteReadTests(AccountRouteFixture):
    def test_each_tab_reads_what_its_nested_route_reads(self):
        self.fill(self.seen)
        for tab in TABS:
            with self.subTest(tab=tab):
                response = self.get(self.viewer, flat(self.seen.pk, tab))
                nested = self.get(
                    self.viewer,
                    f"/api/v1/customers/{self.pizza.pk}/accounts/{self.seen.pk}/{tab}/",
                )
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(len(response.data), 1)
                self.assertEqual(response.data, nested.data)

    def test_an_account_the_viewer_cannot_open_is_a_404_on_every_route(self):
        self.fill(self.hidden)
        for account_id in (self.hidden.pk, self.stranger.pk, 999_999):
            paths = [f"/api/v1/accounts/{account_id}/"] + [flat(account_id, t) for t in TABS]
            for path in paths:
                with self.subTest(path=path):
                    self.assertEqual(self.get(self.viewer, path).status_code, 404)

    def test_an_account_opens_even_when_none_of_its_organisations_does(self):
        taco = Customer.objects.create(organisation=self.org, name="Taco Co", owner=self.colleague)
        unowned = create_account(taco, name="Unowned")
        Note.objects.create(
            account=unowned, title="Hello", author_name="", body="x", logged_at=self.today
        )
        self.assertEqual(self.get(self.viewer, f"/api/v1/customers/{taco.pk}/").status_code, 404)
        response = self.get(self.viewer, flat(unowned.pk, "notes"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

    def test_record_rules_still_apply(self):
        Note.objects.create(
            account=self.seen,
            title="Theirs",
            author=self.colleague,
            author_name="Owner",
            body="x",
            logged_at=self.today,
        )
        Task.objects.create(
            account=self.seen,
            title="Theirs",
            assignee_name="Owner",
            created_by=self.colleague,
            assignee=self.colleague,
            due_date=self.today,
            priority=Task.Priority.LOW,
        )
        Opportunity.objects.create(account=self.seen, title="Eng only", department="engineering")
        for tab in ("notes", "tasks", "opportunities"):
            with self.subTest(tab=tab):
                self.assertEqual(self.get(self.viewer, flat(self.seen.pk, tab)).data, [])
        self.assertEqual(len(self.get(self.admin, flat(self.seen.pk, "opportunities")).data), 1)

    def test_the_detail_reads_and_edits_the_account(self):
        response = self.get(self.viewer, f"/api/v1/accounts/{self.seen.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["name"], "Seen")
        response = self.client.patch(
            f"/api/v1/accounts/{self.seen.pk}/", {"lifecycle_stage": "adoption"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.seen.refresh_from_db()
        self.assertEqual(self.seen.lifecycle_stage, "adoption")
        response = self.client.patch(
            f"/api/v1/accounts/{self.hidden.pk}/", {"lifecycle_stage": "adoption"}, format="json"
        )
        self.assertEqual(response.status_code, 404)
        self.hidden.refresh_from_db()
        self.assertEqual(self.hidden.lifecycle_stage, "onboarding")


class AccountRouteCreateTests(AccountRouteFixture):
    def test_each_create_lands_on_the_account(self):
        self.client.force_authenticate(self.viewer)
        for tab, body in BODIES.items():
            with self.subTest(tab=tab):
                response = self.client.post(flat(self.seen.pk, tab), body, format="json")
                self.assertEqual(response.status_code, 201, response.data)
                row = MODELS[tab].objects.get(pk=response.data["id"])
                self.assertEqual((row.account_id, row.customer_id), (self.seen.pk, None))

    def test_a_file_upload_lands_on_the_account(self):
        self.client.force_authenticate(self.viewer)
        response = self.client.post(
            flat(self.seen.pk, "files"),
            {"file": SimpleUploadedFile("c.pdf", b"%PDF-1.4\n", content_type="application/pdf")},
            format="multipart",
        )
        self.assertEqual(response.status_code, 201, response.data)
        row = Attachment.objects.get(pk=response.data["id"])
        self.assertEqual(
            (row.account_id, row.customer_id, row.organisation_id),
            (self.seen.pk, None, self.org.pk),
        )

    def test_a_ces_survey_is_refused_as_on_the_nested_route(self):
        self.client.force_authenticate(self.viewer)
        response = self.client.post(
            flat(self.seen.pk, "surveys"),
            {"survey_type": "ces", "sent_at": "2026-09-01"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_nothing_is_created_on_an_account_the_viewer_cannot_open(self):
        self.client.force_authenticate(self.viewer)
        for tab, body in BODIES.items():
            with self.subTest(tab=tab):
                response = self.client.post(flat(self.hidden.pk, tab), body, format="json")
                self.assertEqual(response.status_code, 404)
                self.assertFalse(MODELS[tab].objects.filter(account=self.hidden).exists())


class AccountRouteQueryTests(AccountRouteFixture):
    def test_a_flat_route_costs_what_its_nested_route_costs(self):
        self.fill(self.seen)
        self.client.force_authenticate(self.viewer)
        for tab in TABS:
            with self.subTest(tab=tab):
                nested = f"/api/v1/customers/{self.pizza.pk}/accounts/{self.seen.pk}/{tab}/"
                self.client.get(nested)  # the viewer's memoised lookups, read once
                with CaptureQueriesContext(connection) as nested_ctx:
                    self.client.get(nested)
                with CaptureQueriesContext(connection) as flat_ctx:
                    self.client.get(flat(self.seen.pk, tab))
                self.assertEqual(len(flat_ctx), len(nested_ctx))
