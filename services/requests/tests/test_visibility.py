"""Whose feature-request evidence a viewer reads: only what sits on a
company they may open.

An organisation someone may open can carry accounts they may not
(`visible_accounts` is its own rule), and evidence filed on such an account
stays hidden from them on /requests and in the MCP server's tools, which
read `visible_evidence` too."""

from django.utils import timezone
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.customers.models import Account, Customer
from services.requests.gather import visible_evidence
from services.requests.models import FeatureRequest, RequestEvidence

URL = "/api/v1/requests/"


class VisibleEvidenceAccountTests(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        mk = lambda email, name: User.objects.create_user(  # noqa: E731
            email=email, password="x", name=name, organisation=self.org, function=User.Function.CS
        )
        self.dana = mk("dana@acme.io", "Dana")
        self.other = mk("other@acme.io", "Other")
        # Unowned, so Dana may open it; its accounts decide for themselves.
        self.open_co = Customer.objects.create(organisation=self.org, name="Open Co", owner=None)
        self.hidden = Account.objects.create(name="Other's div", owner=self.other)
        self.hidden.customers.add(self.open_co)
        self.shown = Account.objects.create(name="Dana's div", owner=self.dana)
        self.shown.customers.add(self.open_co)
        self.feature = FeatureRequest.objects.create(organisation=self.org, title="SSO")
        self.on_org = self.evidence(self.feature, 1, customer=self.open_co)
        self.on_shown = self.evidence(self.feature, 2, account=self.shown)
        self.on_hidden = self.evidence(self.feature, 3, account=self.hidden)
        self.client.force_authenticate(self.dana)

    def evidence(self, feature, record_id, **parent):
        return RequestEvidence.objects.create(
            request=feature,
            organisation=self.org,
            kind=RequestEvidence.Kind.CALL,
            record_id=record_id,
            snippet=f"ask {record_id}",
            occurred_at=timezone.now(),
            **parent,
        )

    def test_evidence_on_an_account_the_viewer_cannot_open_is_hidden(self):
        rows = set(visible_evidence(self.org, self.dana))
        self.assertEqual(rows, {self.on_org, self.on_shown})

    def test_the_detail_endpoint_shows_only_the_visible_evidence(self):
        response = self.client.get(f"{URL}{self.feature.pk}/")
        self.assertEqual(response.status_code, 200)
        ids = {row["id"] for row in response.json()["evidence"]}
        self.assertEqual(ids, {self.on_org.pk, self.on_shown.pk})

    def test_a_request_only_on_a_hidden_account_is_a_404(self):
        lonely = FeatureRequest.objects.create(organisation=self.org, title="Hidden")
        self.evidence(lonely, 9, account=self.hidden)
        self.assertEqual(self.client.get(f"{URL}{lonely.pk}/").status_code, 404)
        listed = {row["id"] for row in self.client.get(URL).json()}
        self.assertNotIn(lonely.pk, listed)
        self.assertIn(self.feature.pk, listed)
