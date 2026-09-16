"""A contact's sentiment is read from their calls, emails and tickets."""

from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone
from unittest.mock import patch

from django.core.management import call_command
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.customers import contact_sentiment
from services.customers.models import Account, Call, Contact, Customer, Email, Ticket

NOW = timezone.now()


class Fixture(APITestCase):
    def setUp(self):
        from services.copilot.anthropic_client import CopilotNotConfigured

        no_model = patch(
            "services.customers.classification.get_completion",
            side_effect=CopilotNotConfigured("no model in tests"),
        )
        no_model.start()
        self.addCleanup(no_model.stop)
        self.org = Organisation.objects.create(name="Acme")
        self.carl = User.objects.create_user(
            email="carl@acme.io", password="x", name="Carl", organisation=self.org
        )
        self.pizza = Customer.objects.create(organisation=self.org, name="Pizza Hut")
        self.hut_uk = Account.objects.create(name="Pizza Hut UK")
        self.hut_uk.customers.add(self.pizza)
        self.sam = Contact.objects.create(
            customer=self.pizza, name="Sam Pizza", email="sam@pizzahut.com", sentiment="neutral"
        )
        self.uma = Contact.objects.create(
            account=self.hut_uk, name="Uma Hut", email="uma@pizzahut.co.uk", sentiment="positive"
        )

    def call(self, sentiment, days_ago=1, participants=(), **kw):
        call = Call.objects.create(
            customer=self.pizza,
            title=kw.pop("title", "Call"),
            host_name="Carl",
            occurred_at=NOW - timedelta(days=days_ago),
            sentiment=sentiment,
            ai_classified_at=NOW,
            **kw,
        )
        call.participants.set(participants)
        return call


class ScoringTests(Fixture):
    def test_no_evidence_keeps_the_hand_set_value(self):
        self.assertIsNone(contact_sentiment.recompute(self.uma))
        self.uma.refresh_from_db()
        self.assertEqual((self.uma.sentiment, self.uma.sentiment_source), ("positive", "manual"))

    def test_a_negative_call_yesterday_outweighs_an_old_positive_email(self):
        self.call("negative", days_ago=1, participants=[self.sam])
        Email.objects.create(
            customer=self.pizza,
            subject="Thanks!",
            sender_name="Sam",
            recipient_name="Carl",
            body="great",
            sent_at=NOW - timedelta(days=200),
            from_address="sam@pizzahut.com",
            sentiment="positive",
            ai_classified_at=NOW,
        )
        self.assertEqual(contact_sentiment.recompute(self.sam), "negative")
        self.sam.refresh_from_db()
        self.assertEqual(self.sam.sentiment_source, "computed")
        evidence = self.sam.sentiment_evidence
        self.assertEqual((evidence["calls"], evidence["emails"], evidence["tickets"]), (1, 1, 0))
        self.assertEqual((evidence["positive"], evidence["negative"]), (1, 1))
        self.assertLess(evidence["score"], -0.25)
        self.assertEqual(self.sam.last_contacted_at, NOW - timedelta(days=1))

    def test_mixed_recent_evidence_is_neutral_and_tickets_count(self):
        self.call("positive", participants=[self.sam])
        Ticket.objects.create(
            customer=self.pizza,
            ticket_number="ZD-1",
            title="Broken",
            priority="high",
            opened_at=date.today(),
            requester_email="Sam@PizzaHut.com",
            sentiment="negative",
            ai_classified_at=NOW,
        )
        self.assertEqual(contact_sentiment.recompute(self.sam), "neutral")
        self.sam.refresh_from_db()
        self.assertEqual(self.sam.sentiment_evidence["tickets"], 1)

    def test_unclassified_records_are_not_evidence(self):
        Call.objects.create(
            customer=self.pizza, title="x", host_name="h", occurred_at=NOW, sentiment="negative"
        ).participants.set([self.sam])
        self.assertIsNone(contact_sentiment.recompute(self.sam))

    def test_evidence_that_disappears_returns_the_contact_to_hand_set(self):
        call = self.call("negative", participants=[self.sam])
        contact_sentiment.recompute(self.sam)
        call.delete()
        contact_sentiment.recompute(self.sam)
        self.sam.refresh_from_db()
        self.assertEqual((self.sam.sentiment_source, self.sam.sentiment_evidence), ("manual", {}))

    def test_recompute_all_touches_every_contact_in_the_organisation(self):
        self.call("positive", participants=[self.sam])
        other = Organisation.objects.create(name="Other")
        theirs = Customer.objects.create(organisation=other, name="Theirs")
        Contact.objects.create(customer=theirs, name="Ann Other", email="ann@theirs.com")
        self.assertEqual(contact_sentiment.recompute_all(self.org), 1)

    def test_classification_of_a_call_updates_its_participants(self):
        from services.customers.classification import classify_records

        call = Call.objects.create(
            customer=self.pizza, title="Escalation", host_name="Carl", occurred_at=NOW
        )
        call.participants.set([self.sam])
        with patch(
            "services.customers.classification.classify_batch",
            return_value={
                f"call:{call.id}": {
                    "sentiment": "negative",
                    "ai_area": "",
                    "ai_category": "",
                    "ai_subcategory": "",
                }
            },
        ):
            classify_records([call], organisation=self.org)
        self.sam.refresh_from_db()
        self.assertEqual((self.sam.sentiment, self.sam.sentiment_source), ("negative", "computed"))

    def test_the_maintenance_job_recomputes_contacts(self):
        self.call("negative", participants=[self.sam])
        call_command("run_health_maintenance", org_email="carl@acme.io")
        self.sam.refresh_from_db()
        self.assertEqual(self.sam.sentiment, "negative")


class ParticipantTests(Fixture):
    def test_transcript_names_and_addresses_match_this_companys_contacts(self):
        text = "Carl: hi all. SAM PIZZA: we are unhappy. uma@pizzahut.co.uk joined late."
        matched = contact_sentiment.match_participants(self.pizza, None, text)
        self.assertEqual({c.id for c in matched}, {self.sam.id, self.uma.id})
        self.assertEqual(contact_sentiment.match_participants(self.pizza, None, "nobody here"), [])

    def test_logging_a_call_links_chosen_and_named_participants(self):
        self.client.force_authenticate(self.carl)
        outsider_org = Organisation.objects.create(name="Other")
        outsider_customer = Customer.objects.create(organisation=outsider_org, name="Theirs")
        outsider = Contact.objects.create(
            customer=outsider_customer, name="Ann Other", email="ann@theirs.com"
        )
        response = self.client.post(
            f"/api/v1/customers/{self.pizza.id}/calls/",
            {
                "title": "QBR",
                "occurred_at": NOW.isoformat(),
                "summary": "Fine.",
                "participant_ids": [self.uma.id, outsider.id],
                "transcript_text": "Sam Pizza asked for SSO.",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        names = sorted(p["name"] for p in response.data["participants"])
        self.assertEqual(names, ["Sam Pizza", "Uma Hut"])  # the outsider is dropped
        self.assertEqual(response.data["participants"][0]["role_display"], "Other")
        self.sam.refresh_from_db()
        self.assertEqual(self.sam.last_contacted_at.date(), NOW.date())


class ContactApiTests(Fixture):
    def test_the_contact_carries_its_source_and_evidence(self):
        self.call("positive", participants=[self.sam], title="Renewal chat")
        contact_sentiment.recompute(self.sam)
        self.client.force_authenticate(self.carl)
        row = self.client.get(f"/api/v1/contacts/{self.sam.id}/").data
        self.assertEqual((row["sentiment"], row["sentiment_source"]), ("positive", "computed"))
        self.assertEqual(row["sentiment_evidence"]["calls"], 1)
        self.assertIsNotNone(row["sentiment_computed_at"])

        interactions = self.client.get(f"/api/v1/contacts/{self.sam.id}/interactions/")
        self.assertEqual(interactions.status_code, status.HTTP_200_OK)
        self.assertEqual(interactions.data["sentiment"], "positive")
        self.assertEqual(
            [(i["kind"], i["title"], i["sentiment"]) for i in interactions.data["interactions"]],
            [("call", "Renewal chat", "positive")],
        )

    def test_a_hand_edit_marks_the_sentiment_manual_until_evidence_returns(self):
        self.call("positive", participants=[self.sam])
        contact_sentiment.recompute(self.sam)
        self.client.force_authenticate(self.carl)
        response = self.client.patch(
            f"/api/v1/contacts/{self.sam.id}/", {"sentiment": "negative"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(
            (response.data["sentiment"], response.data["sentiment_source"]), ("negative", "manual")
        )
        self.assertEqual(contact_sentiment.recompute(self.sam), "positive")

    def test_interactions_are_scoped(self):
        other = User.objects.create_user(
            email="o@other.io",
            password="x",
            name="O",
            organisation=Organisation.objects.create(name="O"),
        )
        self.client.force_authenticate(other)
        self.assertEqual(
            self.client.get(f"/api/v1/contacts/{self.sam.id}/interactions/").status_code, 404
        )


__all__ = ["datetime", "dt_timezone"]
