"""The send claims the campaign (`sending`) before any email leaves, so a
crash mid-send can never leave a draft that gets sent a second time."""

from unittest.mock import patch

from django.core import mail
from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.campaigns.models import Campaign
from services.customers.models import Contact, Customer
from services.email import send_campaign_email


class WorkerDied(BaseException):
    """Stands in for the process dying mid-send (a timeout kill, an OOM):
    not an `Exception`, so the per-recipient handler doesn't swallow it."""


class CampaignSendClaimTests(APITestCase):
    def setUp(self):
        org = Organisation.objects.create(name="Acme Inc")
        self.user = User.objects.create_user(
            email="alice@acme.io", password="supersecret1", name="Alice", organisation=org
        )
        customer = Customer.objects.create(organisation=org, name="Globex")
        self.campaign = Campaign.objects.create(
            organisation=org, name="Renewal", subject="Hi", body="Body"
        )
        self.campaign.recipients.set(
            [
                Contact.objects.create(customer=customer, name="Ann", email="ann@x.example"),
                Contact.objects.create(customer=customer, name="Bob", email="bob@x.example"),
            ]
        )
        self.client.force_authenticate(self.user)
        self.detail = f"/api/v1/campaigns/{self.campaign.id}/"

    def _mark_sending(self):
        Campaign.objects.filter(pk=self.campaign.pk).update(status="sending")

    def _crash_after_first_email(self):
        calls = []

        def mailer(*args, **kwargs):
            if calls:
                raise WorkerDied
            calls.append(1)
            return send_campaign_email(*args, **kwargs)

        with patch("services.campaigns.views.send_campaign_email", side_effect=mailer):
            with self.assertRaises(WorkerDied):
                self.client.post(f"{self.detail}send/")

    def test_patch_is_refused_while_sending(self):
        self._mark_sending()
        response = self.client.patch(self.detail, {"name": "Renamed"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.name, "Renewal")

    def test_delete_is_refused_while_sending(self):
        self._mark_sending()
        response = self.client.delete(self.detail)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(Campaign.objects.filter(pk=self.campaign.pk).exists())

    def test_a_crash_mid_send_leaves_the_campaign_sending(self):
        self._crash_after_first_email()

        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, "sending")
        self.assertEqual(len(mail.outbox), 1)

    def test_no_second_send_after_a_crash(self):
        self._crash_after_first_email()

        response = self.client.post(f"{self.detail}send/")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(self.client.get(self.detail).data["status_display"], "Sending")

    def test_a_finished_send_ends_sent_with_its_log(self):
        response = self.client.post(f"{self.detail}send/")

        self.assertEqual(response.data["status"], "sent")
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, Campaign.Status.SENT)
        self.assertEqual(len(self.campaign.send_log), 2)
        self.assertIsNotNone(self.campaign.sent_at)
