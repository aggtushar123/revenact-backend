"""Twice filtered: a campaign is org-wide, but a recipient is shown only to a
reader who may open that contact (visible_children_q). One the reader can't
see reads like one that doesn't exist — no name, no email, no log line —
and is only counted in `hidden_recipients`."""

from django.core import mail
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import AuditEvent
from services.accounts.models import Organisation, User
from services.campaigns.models import Campaign
from services.customers.models import Contact, Customer
from services.customers.tests.test_views import blind_to_one_account


class CampaignRecipientPrivacyTests(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.customer = Customer.objects.create(organisation=self.org, name="Globex")
        self.viewer, self.seen, self.hidden = blind_to_one_account(self.customer)
        self.admin = User.objects.create_user(
            email="admin@acme.io",
            password="supersecret1",
            name="Admin",
            organisation=self.org,
            role=User.Role.ADMIN,
        )
        self.org_contact = Contact.objects.create(
            customer=self.customer, name="Jane Doe", email="jane@globex.example"
        )
        self.seen_contact = Contact.objects.create(
            account=self.seen, name="Sam Seen", email="sam@seen.example"
        )
        self.hidden_contact = Contact.objects.create(
            account=self.hidden, name="Hugo Hidden", email="hugo@hidden.example"
        )
        self.campaign = Campaign.objects.create(
            organisation=self.org, name="Renewal", subject="Hi", body="Body"
        )
        self.campaign.recipients.set([self.org_contact, self.seen_contact, self.hidden_contact])

    def _detail(self):
        return f"/api/v1/campaigns/{self.campaign.id}/"

    def assertNamesNobodyHidden(self, response):
        body = response.content.decode()
        self.assertNotIn("Hugo Hidden", body)
        self.assertNotIn("hugo@hidden.example", body)
        self.assertNotIn(f'"contact_id":{self.hidden_contact.id}', body.replace(" ", ""))

    def test_list_hides_a_recipient_the_reader_cannot_see(self):
        self.client.force_authenticate(self.viewer)
        response = self.client.get("/api/v1/campaigns/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        row = response.data[0]
        self.assertEqual(
            sorted(r["id"] for r in row["recipients"]),
            sorted([self.org_contact.id, self.seen_contact.id]),
        )
        self.assertEqual(row["hidden_recipients"], 1)
        self.assertNamesNobodyHidden(response)

    def test_detail_hides_a_recipient_the_reader_cannot_see(self):
        self.client.force_authenticate(self.viewer)
        response = self.client.get(self._detail())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["recipients"]), 2)
        self.assertEqual(response.data["hidden_recipients"], 1)
        self.assertNamesNobodyHidden(response)

    def test_update_keeps_the_recipients_the_editor_cannot_see(self):
        """The editor sends back the list it was shown; replacing the whole
        set with it would silently drop the hidden one."""
        self.client.force_authenticate(self.viewer)
        response = self.client.patch(
            self._detail(), {"recipient_ids": [self.seen_contact.id]}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            set(self.campaign.recipients.values_list("id", flat=True)),
            {self.seen_contact.id, self.hidden_contact.id},
        )
        self.assertEqual([r["id"] for r in response.data["recipients"]], [self.seen_contact.id])
        self.assertEqual(response.data["hidden_recipients"], 1)
        self.assertNamesNobodyHidden(response)

    def test_update_still_refuses_adding_a_contact_the_editor_cannot_see(self):
        self.client.force_authenticate(self.viewer)
        response = self.client.patch(
            self._detail(), {"recipient_ids": [self.hidden_contact.id]}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertNamesNobodyHidden(response)

    def test_send_log_is_filtered_for_the_sender_and_later_readers(self):
        self.client.force_authenticate(self.viewer)
        response = self.client.post(f"{self._detail()}send/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            sorted(e["contact_id"] for e in response.data["send_log"]),
            sorted([self.org_contact.id, self.seen_contact.id]),
        )
        self.assertEqual(response.data["hidden_recipients"], 1)
        self.assertNamesNobodyHidden(response)
        # Stored whole: the admin's view below, and the audit trail, rely on it.
        self.campaign.refresh_from_db()
        self.assertEqual(len(self.campaign.send_log), 3)
        self.assertEqual(len(mail.outbox), 3)

        self.assertNamesNobodyHidden(self.client.get(self._detail()))
        self.assertNamesNobodyHidden(self.client.get("/api/v1/campaigns/"))

    def test_admin_sees_every_recipient_and_the_whole_log(self):
        self.client.force_authenticate(self.admin)
        self.client.post(f"{self._detail()}send/")

        response = self.client.get(self._detail())

        self.assertEqual(len(response.data["recipients"]), 3)
        self.assertEqual(response.data["hidden_recipients"], 0)
        self.assertEqual(len(response.data["send_log"]), 3)
        self.assertIn("Hugo Hidden", response.content.decode())

    def test_list_query_count_stays_flat_as_recipients_grow(self):
        # A fresh user object per request, as in production: the membership
        # and org-chart lookups are cached on the instance.
        self.client.force_authenticate(User.objects.get(pk=self.viewer.pk))
        # organisation, membership, role, org chart, campaigns, visible recipients
        with self.assertNumQueries(6):
            self.client.get("/api/v1/campaigns/")

        for i in range(3):
            more = Campaign.objects.create(organisation=self.org, name=f"More {i}")
            more.recipients.set(
                [
                    Contact.objects.create(
                        account=account, name=f"P{i}{j}", email=f"p{i}{j}@x.example"
                    )
                    for j, account in enumerate([self.seen, self.hidden] * 5)
                ]
            )
        self.client.force_authenticate(User.objects.get(pk=self.viewer.pk))
        with self.assertNumQueries(6):
            response = self.client.get("/api/v1/campaigns/")
        self.assertEqual(len(response.data), 4)
        self.assertEqual(sorted(row["hidden_recipients"] for row in response.data), [1, 5, 5, 5])


class CampaignAuditTests(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.user = User.objects.create_user(
            email="alice@acme.io", password="supersecret1", name="Alice", organisation=self.org
        )
        customer = Customer.objects.create(organisation=self.org, name="Globex")
        self.contact = Contact.objects.create(
            customer=customer, name="Jane Doe", email="jane@globex.example"
        )
        self.client.force_authenticate(self.user)

    def _event(self, action):
        event = AuditEvent.objects.get(action=action)
        self.assertEqual(event.actor, self.user)
        self.assertEqual(event.target_type, "campaigns.campaign")
        text = f"{event.target_repr} {event.metadata}"
        self.assertNotIn("Secret Plan", text)
        self.assertNotIn("Jane", text)
        self.assertNotIn("jane@", text)
        return event

    def test_create_update_send_delete_are_audited_with_ids_only(self):
        response = self.client.post(
            "/api/v1/campaigns/",
            {
                "name": "Secret Plan",
                "subject": "Hi",
                "body": "Body",
                "recipient_ids": [self.contact.id],
            },
            format="json",
        )
        campaign_id = response.data["id"]
        created = self._event("campaign.created")
        self.assertEqual(created.target_id, str(campaign_id))
        self.assertEqual(created.metadata, {"recipient_ids": [self.contact.id]})

        url = f"/api/v1/campaigns/{campaign_id}/"
        self.client.patch(url, {"subject": "Hello"}, format="json")
        self.assertEqual(self._event("campaign.updated").metadata["fields"], ["subject"])

        self.client.post(f"{url}send/")
        sent = self._event("campaign.sent")
        self.assertEqual(sent.metadata, {"sent": 1, "skipped": 0})

        self.client.delete(url)
        self.assertEqual(self._event("campaign.deleted").target_id, str(campaign_id))
