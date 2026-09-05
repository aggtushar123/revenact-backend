"""Integration tier: through the real URLconf + real test DB."""

from django.core import mail
from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.campaigns.models import Campaign
from services.customers.models import Account, Contact, Customer, Email


class CampaignListCreateTests(APITestCase):
    url = "/api/v1/campaigns/"

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.other_org = Organisation.objects.create(name="Other Inc")
        self.user = User.objects.create_user(
            email="alice@acme.io", password="supersecret1", name="Alice", organisation=self.org
        )
        self.customer = Customer.objects.create(organisation=self.org, name="Globex")
        self.contact = Contact.objects.create(
            customer=self.customer, name="Jane Doe", email="jane@globex.example"
        )

    def test_unauthenticated_cannot_list(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_list_only_returns_own_organisation(self):
        Campaign.objects.create(organisation=self.org, name="Mine")
        Campaign.objects.create(organisation=self.other_org, name="Not mine")
        self.client.force_authenticate(self.user)

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        names = [c["name"] for c in response.data]
        self.assertEqual(names, ["Mine"])

    def test_create_defaults_and_scopes_to_caller_organisation(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(self.url, {"name": "Renewal Reminder"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        campaign = Campaign.objects.get(pk=response.data["id"])
        self.assertEqual(campaign.organisation, self.org)
        self.assertEqual(campaign.status, Campaign.Status.DRAFT)

    def test_recipient_ids_resolves_real_contacts(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            self.url,
            {"name": "Renewal Reminder", "recipient_ids": [self.contact.id]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            response.data["recipients"],
            [{"id": self.contact.id, "name": "Jane Doe", "email": "jane@globex.example"}],
        )

    def test_recipient_ids_rejects_another_organisations_contact(self):
        other_customer = Customer.objects.create(organisation=self.other_org, name="Initech")
        foreign_contact = Contact.objects.create(
            customer=other_customer, name="Bob", email="bob@initech.example"
        )
        self.client.force_authenticate(self.user)

        response = self.client.post(
            self.url,
            {"name": "Renewal Reminder", "recipient_ids": [foreign_contact.id]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        campaign = Campaign.objects.get(pk=response.data["id"])
        self.assertEqual(list(campaign.recipients.all()), [])


class CampaignDetailTests(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.other_org = Organisation.objects.create(name="Other Inc")
        self.user = User.objects.create_user(
            email="alice@acme.io", password="supersecret1", name="Alice", organisation=self.org
        )
        self.campaign = Campaign.objects.create(organisation=self.org, name="Mine")
        self.foreign_campaign = Campaign.objects.create(
            organisation=self.other_org, name="Not mine"
        )

    def test_patch_updates_subject_and_body(self):
        self.client.force_authenticate(self.user)
        url = f"/api/v1/campaigns/{self.campaign.id}/"
        response = self.client.patch(url, {"subject": "Hello", "body": "World"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.subject, "Hello")
        self.assertEqual(self.campaign.body, "World")

    def test_cannot_patch_a_sent_campaign(self):
        self.campaign.status = Campaign.Status.SENT
        self.campaign.save()
        self.client.force_authenticate(self.user)

        url = f"/api/v1/campaigns/{self.campaign.id}/"
        response = self.client.patch(url, {"name": "Renamed"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.name, "Mine")

    def test_404_for_campaign_outside_own_organisation(self):
        self.client.force_authenticate(self.user)
        url = f"/api/v1/campaigns/{self.foreign_campaign.id}/"
        self.assertEqual(self.client.get(url).status_code, status.HTTP_404_NOT_FOUND)

    def test_delete(self):
        self.client.force_authenticate(self.user)
        url = f"/api/v1/campaigns/{self.campaign.id}/"
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Campaign.objects.filter(pk=self.campaign.id).exists())


class CampaignSendViewTests(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.user = User.objects.create_user(
            email="alice@acme.io", password="supersecret1", name="Alice", organisation=self.org
        )
        self.customer = Customer.objects.create(organisation=self.org, name="Globex")
        self.account = Account.objects.create(name="Globex EMEA")
        self.account.customers.add(self.customer)
        self.has_email = Contact.objects.create(
            customer=self.customer, name="Jane Doe", email="jane@globex.example"
        )
        self.account_contact = Contact.objects.create(
            account=self.account, name="Bob Smith", email="bob@globex.example"
        )
        self.campaign = Campaign.objects.create(
            organisation=self.org, name="Renewal Reminder", subject="Hi", body="Body"
        )
        self.campaign.recipients.set([self.has_email, self.account_contact])

    def _url(self, campaign):
        return f"/api/v1/campaigns/{campaign.id}/send/"

    def test_send_emails_every_recipient_and_logs_the_outcome(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(self._url(self.campaign))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "sent")
        self.assertEqual(response.data["sent_count"], 2)
        self.assertEqual(response.data["skipped_count"], 0)
        self.assertEqual(len(mail.outbox), 2)
        self.assertIsNotNone(response.data["sent_at"])

    def test_send_creates_a_real_email_row_per_recipient_on_the_right_parent(self):
        self.client.force_authenticate(self.user)
        self.client.post(self._url(self.campaign))

        org_level_email = Email.objects.get(customer=self.customer)
        self.assertEqual(org_level_email.recipient_name, "Jane Doe")
        self.assertEqual(org_level_email.campaign, self.campaign)

        account_level_email = Email.objects.get(account=self.account)
        self.assertEqual(account_level_email.recipient_name, "Bob Smith")
        self.assertEqual(account_level_email.campaign, self.campaign)

    def test_a_recipient_with_no_email_is_skipped_not_fatal(self):
        # A blank email bypasses model-level validation via a direct
        # .create() the same way a real bulk import could — Contact's
        # own EmailField isn't blank=True, but nothing stops a row from
        # ending up with "" since plain .save() never runs full_clean().
        no_email_contact = Contact.objects.create(customer=self.customer, name="No Email", email="")
        self.campaign.recipients.add(no_email_contact)
        self.client.force_authenticate(self.user)

        response = self.client.post(self._url(self.campaign))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["sent_count"], 2)
        self.assertEqual(response.data["skipped_count"], 1)
        self.assertEqual(len(mail.outbox), 2)

    def test_cannot_send_twice(self):
        self.client.force_authenticate(self.user)
        self.client.post(self._url(self.campaign))

        response = self.client.post(self._url(self.campaign))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(len(mail.outbox), 2)

    def test_cannot_send_with_no_subject_or_body(self):
        campaign = Campaign.objects.create(organisation=self.org, name="Blank")
        campaign.recipients.add(self.has_email)
        self.client.force_authenticate(self.user)

        response = self.client.post(self._url(campaign))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_send_with_no_recipients(self):
        campaign = Campaign.objects.create(
            organisation=self.org, name="No Recipients", subject="Hi", body="Body"
        )
        self.client.force_authenticate(self.user)

        response = self.client.post(self._url(campaign))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_404_for_campaign_outside_own_organisation(self):
        other_org = Organisation.objects.create(name="Other Inc")
        other_campaign = Campaign.objects.create(organisation=other_org, name="Not mine")
        self.client.force_authenticate(self.user)

        response = self.client.post(self._url(other_campaign))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
