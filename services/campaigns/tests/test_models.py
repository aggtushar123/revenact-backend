"""Unit tier: model defaults, no HTTP."""

from django.test import TestCase

from services.accounts.models import Organisation
from services.campaigns.models import Campaign
from services.customers.models import Contact, Customer


class CampaignDefaultsTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")

    def test_defaults(self):
        campaign = Campaign.objects.create(organisation=self.org)
        self.assertEqual(campaign.name, "Untitled Campaign")
        self.assertEqual(campaign.subject, "")
        self.assertEqual(campaign.body, "")
        self.assertEqual(campaign.status, Campaign.Status.DRAFT)
        self.assertEqual(campaign.send_log, [])
        self.assertIsNone(campaign.sent_at)
        self.assertEqual(list(campaign.recipients.all()), [])

    def test_ordered_most_recently_updated_first(self):
        older = Campaign.objects.create(organisation=self.org, name="Older")
        newer = Campaign.objects.create(organisation=self.org, name="Newer")
        self.assertEqual(list(Campaign.objects.all()), [newer, older])

    def test_recipients_is_a_real_many_to_many_to_contact(self):
        customer = Customer.objects.create(organisation=self.org, name="Globex")
        contact = Contact.objects.create(
            customer=customer, name="Jane Doe", email="jane@globex.example"
        )
        campaign = Campaign.objects.create(organisation=self.org)

        campaign.recipients.add(contact)

        self.assertEqual(list(campaign.recipients.all()), [contact])
        self.assertEqual(list(contact.campaigns.all()), [campaign])
