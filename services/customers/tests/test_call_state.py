"""A call is `pending`, `analysed` or `not_analysable`: read with nothing to
judge, stamped so no pass pays to look again, and never guessed."""

from datetime import datetime
from datetime import timezone as dt_timezone

from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.customers.classification import apply_classification, mark_not_analysable
from services.customers.models import Account, Attachment, Call, Customer

WHEN = datetime(2026, 9, 20, 10, 0, tzinfo=dt_timezone.utc)


class CallFixture(APITestCase):
    """Carl, an admin at Acme, with Pizza Hut and its EMEA account. Shared by
    the call tests of this delivery; it has no tests of its own."""

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme")
        self.carl = User.objects.create_user(
            email="carl@acme.io",
            password="x",
            name="Carl",
            organisation=self.org,
            role=User.Role.ADMIN,
        )
        self.pizza = Customer.objects.create(organisation=self.org, name="Pizza Hut")
        self.emea = Account.objects.create(name="EMEA")
        self.emea.customers.add(self.pizza)

    def call(self, title="Renewal readiness", summary="", **kw):
        kw.setdefault("customer", self.pizza)
        return Call.objects.create(
            title=title, summary=summary, host_name="Carl", occurred_at=WHEN, **kw
        )

    def transcript(self, text, name="qbr.txt"):
        return Attachment.objects.create(
            organisation=self.org,
            customer=self.pizza,
            file=SimpleUploadedFile(name, text.encode(), content_type="text/plain"),
            name=name,
            content_type="text/plain",
            size=len(text),
            source=Attachment.Source.TRANSCRIPT,
        )

    def binary_transcript(self, data: bytes, name="blob.bin"):
        return Attachment.objects.create(
            organisation=self.org,
            customer=self.pizza,
            file=SimpleUploadedFile(name, data, content_type="application/octet-stream"),
            name=name,
            content_type="application/octet-stream",
            size=len(data),
            source=Attachment.Source.TRANSCRIPT,
        )


class NotAnalysableTests(CallFixture):
    def test_a_new_call_is_pending(self):
        call = self.call()
        self.assertEqual((call.analysis, call.not_analysable), ("pending", False))

    def test_marking_stamps_it_and_blanks_the_reading(self):
        call = self.call(
            title="Sync", sentiment="negative", ai_category="onboarding", ai_area="customer_success"
        )
        mark_not_analysable(call)
        call.refresh_from_db()
        self.assertEqual(call.analysis, "not_analysable")
        self.assertIsNotNone(call.ai_classified_at)
        self.assertEqual((call.sentiment, call.ai_area, call.ai_category), ("neutral", "", ""))

    def test_a_reading_is_analysed(self):
        call = self.call()
        apply_classification(call, {"sentiment": "positive", "ai_category": "onboarding"})
        self.assertEqual(call.analysis, "analysed")

    def test_a_later_reading_clears_the_mark(self):
        call = self.call()
        mark_not_analysable(call)
        apply_classification(call, {"sentiment": "positive", "ai_category": "onboarding"})
        call.refresh_from_db()
        self.assertEqual((call.analysis, call.sentiment), ("analysed", "positive"))

    def test_the_calls_list_says_how_each_call_was_read(self):
        mark_not_analysable(self.call(title="Sync"))
        self.call(title="QBR")
        self.client.force_authenticate(self.carl)
        rows = self.client.get(f"/api/v1/customers/{self.pizza.id}/calls/").data
        self.assertEqual(
            {r["title"]: r["analysis"] for r in rows}, {"Sync": "not_analysable", "QBR": "pending"}
        )
