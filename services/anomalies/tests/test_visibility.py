"""Whose evidence a viewer reads: only what sits on a company they may open.

An organisation someone may open can carry accounts they may not
(`visible_accounts` is its own rule), and evidence filed on such an account
stays hidden from them on /anomalies, and everywhere else that reads
`visible_evidence`."""

from django.utils import timezone
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.anomalies.models import Anomaly, AnomalyEvidence
from services.anomalies.views import visible_evidence
from services.customers.models import Account, Customer

URL = "/api/v1/anomalies/"


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
        now = timezone.now()
        self.anomaly = Anomaly.objects.create(
            organisation=self.org, title="SSO broke", first_seen_at=now, last_seen_at=now
        )
        self.on_org = self.evidence(1, customer=self.open_co)
        self.on_shown = self.evidence(2, account=self.shown)
        self.on_hidden = self.evidence(3, account=self.hidden)
        self.client.force_authenticate(self.dana)

    def evidence(self, record_id, **parent):
        return AnomalyEvidence.objects.create(
            anomaly=self.anomaly,
            organisation=self.org,
            kind=AnomalyEvidence.Kind.CALL,
            record_id=record_id,
            snippet=f"report {record_id}",
            occurred_at=timezone.now(),
            **parent,
        )

    def test_evidence_on_an_account_the_viewer_cannot_open_is_hidden(self):
        rows = set(visible_evidence(self.org, self.dana))
        self.assertEqual(rows, {self.on_org, self.on_shown})

    def test_the_detail_endpoint_shows_only_the_visible_evidence(self):
        response = self.client.get(f"{URL}{self.anomaly.pk}/")
        self.assertEqual(response.status_code, 200)
        ids = {row["id"] for row in response.json()["evidence"]}
        self.assertEqual(ids, {self.on_org.pk, self.on_shown.pk})

    def test_a_cluster_only_on_a_hidden_account_is_a_404(self):
        now = timezone.now()
        lonely = Anomaly.objects.create(
            organisation=self.org, title="Hidden", first_seen_at=now, last_seen_at=now
        )
        AnomalyEvidence.objects.create(
            anomaly=lonely,
            organisation=self.org,
            kind=AnomalyEvidence.Kind.CALL,
            record_id=9,
            snippet="secret",
            occurred_at=now,
            account=self.hidden,
        )
        self.assertEqual(self.client.get(f"{URL}{lonely.pk}/").status_code, 404)
        listed = {row["id"] for row in self.client.get(URL).json()}
        self.assertNotIn(lonely.pk, listed)
        self.assertIn(self.anomaly.pk, listed)
