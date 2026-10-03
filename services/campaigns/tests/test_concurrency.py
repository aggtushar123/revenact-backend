"""Two sends of the same campaign at once must email its recipients once.

A real race, so a TransactionTestCase with two threads, each on its own
database connection: the send is slowed down so both requests are in
flight together, which is exactly when an unlocked status check lets both
through."""

import threading
import time
from unittest.mock import patch

from django.core import mail
from django.db import connection
from django.test import TransactionTestCase
from rest_framework.test import APIClient

from services.accounts.models import Organisation, User
from services.campaigns.models import Campaign
from services.customers.models import Contact, Customer
from services.email import send_campaign_email


def slow_send(*args, **kwargs):
    time.sleep(0.3)
    return send_campaign_email(*args, **kwargs)


class CampaignDoubleSendTests(TransactionTestCase):
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
                Contact.objects.create(customer=customer, name="Jane", email="jane@x.example"),
                Contact.objects.create(customer=customer, name="Bob", email="bob@x.example"),
            ]
        )

    def test_two_sends_at_once_email_each_recipient_once(self):
        url = f"/api/v1/campaigns/{self.campaign.id}/send/"
        start = threading.Barrier(2)
        codes = []

        def send():
            try:
                client = APIClient()
                client.force_authenticate(User.objects.get(pk=self.user.pk))
                start.wait()
                codes.append(client.post(url).status_code)
            finally:
                connection.close()

        with patch("services.campaigns.views.send_campaign_email", side_effect=slow_send):
            threads = [threading.Thread(target=send) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        self.assertEqual(sorted(codes), [200, 400])
        self.assertEqual(len(mail.outbox), 2)
