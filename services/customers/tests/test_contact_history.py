"""GET /api/v1/contacts/<id>/history/ — a person's calls, emails and tickets,
each under its own record rule, and a 404 for a contact the viewer cannot
open. /interactions/ is retired: the endpoint no longer exists."""

from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone

from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.customers.classification import mark_not_analysable
from services.customers.models import Account, Call, Contact, Customer, Email, Ticket
from services.customers.tests.test_views import blind_to_one_account

WHEN = datetime(2026, 9, 20, 10, 0, tzinfo=dt_timezone.utc)


class Fixture(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme")
        self.pizza = Customer.objects.create(organisation=self.org, name="Pizza Hut")
        # The viewer opens Pizza Hut and its Seen account, not its Hidden one,
        # which the colleague (Pizza Hut's owner) owns.
        self.viewer, self.seen, self.hidden = blind_to_one_account(self.pizza)
        self.colleague = self.pizza.owner
        self.sam = Contact.objects.create(
            customer=self.pizza, name="Sam Pizza", email="sam@pizzahut.com"
        )
        self.url = f"/api/v1/contacts/{self.sam.id}/history/"

    def call(self, title, days_ago=0, sentiment="positive", **parent):
        parent = parent or {"customer": self.pizza}
        call = Call.objects.create(
            title=title,
            host_name="Carl",
            summary=f"{title} summary",
            occurred_at=WHEN - timedelta(days=days_ago),
            duration_minutes=30,
            sentiment=sentiment,
            ai_category="onboarding",
            ai_classified_at=WHEN,
            **parent,
        )
        call.participants.add(self.sam)
        return call

    def email(self, subject, mailbox_owner=None, customer=None, **kw):
        return Email.objects.create(
            customer=customer or self.pizza,
            subject=subject,
            sender_name="Sam",
            recipient_name="Carl",
            body="Body text.",
            sent_at=WHEN,
            from_address="Sam@PizzaHut.com",
            mailbox_owner=mailbox_owner,
            **kw,
        )

    def ticket(self, number, department="", customer=None):
        return Ticket.objects.create(
            customer=customer or self.pizza,
            ticket_number=number,
            title="Broken export",
            priority="high",
            opened_at=date(2026, 9, 19),
            requester_email="sam@pizzahut.com",
            department=department,
            external_url="https://acme.zendesk.com/t/1",
        )

    def read(self, user=None):
        self.client.force_authenticate(user or self.viewer)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return response.data


class HistoryShapeTests(Fixture):
    def test_calls_come_newest_first_with_their_reading_and_parents(self):
        self.call("Older", days_ago=3, sentiment="negative")
        seen = self.call("On Seen", days_ago=1, account=self.seen)
        empty = self.call("Sync", days_ago=0)
        mark_not_analysable(empty)

        body = self.read()

        self.assertEqual([c["title"] for c in body["calls"]], ["Sync", "On Seen", "Older"])
        sync, on_seen, older = body["calls"]
        self.assertEqual((sync["analysis"], sync["sentiment"]), ("not_analysable", None))
        self.assertEqual((older["analysis"], older["sentiment"]), ("analysed", "negative"))
        self.assertEqual(older["classification"]["category"], "Onboarding")
        self.assertEqual(
            (on_seen["organisation"], on_seen["account"]),
            ({"id": self.pizza.id, "name": "Pizza Hut"}, {"id": self.seen.id, "name": "Seen"}),
        )
        self.assertEqual(older["organisation"], {"id": self.pizza.id, "name": "Pizza Hut"})
        self.assertIsNone(older["account"])
        self.assertEqual(
            set(on_seen),
            {
                "id",
                "title",
                "occurred_at",
                "duration_minutes",
                "host_name",
                "summary",
                "analysis",
                "sentiment",
                "classification",
                "organisation",
                "account",
                "link",
            },
        )
        self.assertEqual(on_seen["id"], seen.id)
        self.assertEqual(body["counts"], {"calls": 3, "emails": 0, "tickets": 0})

    def test_an_account_linked_to_a_hidden_organisation_names_only_the_visible_one(self):
        # Joint is linked to two organisations: "Mine", the viewer's own,
        # and "AAA Hidden", the colleague's — named so it sorts first,
        # proving `_parents` picks by visibility, not by order.
        mine = Customer.objects.create(organisation=self.org, name="Mine", owner=self.viewer)
        secret = Customer.objects.create(
            organisation=self.org, name="AAA Hidden", owner=self.colleague
        )
        joint = Account.objects.create(name="Joint", owner=self.colleague)
        joint.customers.add(mine, secret)
        self.call("On Joint", account=joint)

        row = self.read()["calls"][0]

        self.assertEqual(row["organisation"], {"id": mine.id, "name": "Mine"})
        self.assertEqual(row["account"], {"id": joint.id, "name": "Joint"})

    def test_an_unread_call_is_pending_with_no_sentiment(self):
        Call.objects.create(
            customer=self.pizza, title="New", host_name="Carl", occurred_at=WHEN
        ).participants.add(self.sam)
        row = self.read()["calls"][0]
        self.assertEqual((row["analysis"], row["sentiment"]), ("pending", None))

    def test_emails_and_tickets_carry_their_reading_and_links(self):
        self.email("Thanks", sentiment="positive", ai_classified_at=WHEN, thread_id="t-1")
        self.ticket("ZD-1")

        body = self.read()

        email = body["emails"][0]
        self.assertEqual(
            (email["subject"], email["analysis"], email["sentiment"], email["link"]),
            ("Thanks", "analysed", "positive", {"thread_id": "t-1"}),
        )
        ticket = body["tickets"][0]
        self.assertEqual(
            (ticket["ticket_number"], ticket["analysis"], ticket["sentiment"]),
            ("ZD-1", "pending", None),
        )
        self.assertEqual(ticket["link"], {"url": "https://acme.zendesk.com/t/1"})

    def test_a_ticket_row_names_its_department(self):
        """The page reads "Department · Status"; no department reads blank."""
        self.ticket("ZD-1", department="cs")
        self.ticket("ZD-2")
        rows = {t["ticket_number"]: t for t in self.read()["tickets"]}
        self.assertEqual(
            (rows["ZD-1"]["department"], rows["ZD-1"]["department_display"]),
            ("cs", "Customer Success"),
        )
        self.assertEqual((rows["ZD-2"]["department"], rows["ZD-2"]["department_display"]), ("", ""))

    def test_the_breakdown_comes_with_it(self):
        from services.customers.contact_sentiment import recompute

        self.call("QBR")
        recompute(self.sam)
        body = self.read()
        self.assertEqual((body["sentiment"], body["sentiment_source"]), ("positive", "computed"))
        self.assertEqual(body["sentiment_readable"]["calls"], 1)
        self.assertNotIn("sentiment_evidence", body)


class HistoryPrivacyTests(Fixture):
    def test_a_call_on_an_account_the_viewer_cannot_open_is_left_out(self):
        self.call("Org call")
        self.call("Seen call", account=self.seen)
        self.call("Hidden call", account=self.hidden)

        body = self.read()

        self.assertEqual({c["title"] for c in body["calls"]}, {"Org call", "Seen call"})
        self.assertEqual(body["counts"]["calls"], 2)
        titles = {c["title"] for c in self.read(self.colleague)["calls"]}
        self.assertEqual(titles, {"Org call", "Seen call", "Hidden call"})

    def test_mail_follows_its_mailbox_owner(self):
        self.email("Shared")
        self.email("Mine", mailbox_owner=self.viewer)
        self.email("Colleague's", mailbox_owner=self.colleague)

        self.assertEqual({e["subject"] for e in self.read()["emails"]}, {"Shared", "Mine"})
        self.assertEqual(
            {e["subject"] for e in self.read(self.colleague)["emails"]}, {"Shared", "Colleague's"}
        )

    def test_mail_on_a_hidden_account_is_left_out(self):
        Email.objects.create(
            account=self.hidden,
            subject="Hidden",
            sender_name="Sam",
            recipient_name="Carl",
            body="x",
            sent_at=WHEN,
            from_address="sam@pizzahut.com",
        )
        self.assertEqual(self.read()["emails"], [])

    def test_tickets_follow_their_department(self):
        self.ticket("ZD-1")
        self.ticket("ZD-2", department=User.Function.CS)
        self.ticket("ZD-3", department=User.Function.ENGINEERING)

        numbers = {t["ticket_number"] for t in self.read()["tickets"]}
        self.assertEqual(numbers, {"ZD-1", "ZD-2"})
        self.viewer.function = User.Function.LEADERSHIP
        self.viewer.save(update_fields=["function"])
        numbers = {t["ticket_number"] for t in self.read(self.viewer)["tickets"]}
        self.assertEqual(numbers, {"ZD-1", "ZD-2", "ZD-3"})

    def test_another_tenants_records_from_the_same_address_are_left_out(self):
        other = Organisation.objects.create(name="Other")
        theirs = Customer.objects.create(organisation=other, name="Theirs")
        self.email("Theirs", customer=theirs)
        self.ticket("OT-1", customer=theirs)
        body = self.read()
        self.assertEqual((body["emails"], body["tickets"]), ([], []))

    def test_a_contact_the_viewer_cannot_open_is_a_404(self):
        hidden_contact = Contact.objects.create(
            account=self.hidden, name="Hal Hidden", email="hal@pizzahut.com"
        )
        self.client.force_authenticate(self.viewer)
        for pk in (hidden_contact.id, 999999):
            with self.subTest(pk=pk):
                response = self.client.get(f"/api/v1/contacts/{pk}/history/")
                self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        outsider = User.objects.create_user(
            email="o@other.io",
            password="x",
            name="O",
            organisation=Organisation.objects.create(name="O"),
        )
        self.client.force_authenticate(outsider)
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_404_NOT_FOUND)

    def test_the_retired_interactions_endpoint_is_gone(self):
        self.client.force_authenticate(self.viewer)
        response = self.client.get(f"/api/v1/contacts/{self.sam.id}/interactions/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class SentimentReadableTests(Fixture):
    """`sentiment_readable`: the same evidence a computed sentiment rests
    on (contact_sentiment.interactions_for), counted only over what the
    viewer may actually open — never `sentiment_evidence`, which is gone
    from every response."""

    def build(self):
        from services.customers.contact_sentiment import recompute

        self.call("Seen call", days_ago=1, account=self.seen, sentiment="positive")
        self.call("Hidden call", days_ago=2, account=self.hidden, sentiment="negative")
        self.email(
            "Colleague's", mailbox_owner=self.colleague, sentiment="negative", ai_classified_at=WHEN
        )
        Ticket.objects.create(
            customer=self.pizza,
            ticket_number="ZD-9",
            title="Broken export",
            priority="high",
            opened_at=date(2026, 9, 18),
            requester_email="sam@pizzahut.com",
            department=User.Function.ENGINEERING,
            ai_classified_at=WHEN,
            sentiment="negative",
        )
        recompute(self.sam)

    def test_counts_only_the_viewers_readable_evidence(self):
        self.build()
        body = self.read()
        self.assertEqual(
            body["sentiment_readable"],
            {
                "calls": 1,
                "emails": 0,
                "tickets": 0,
                "positive": 1,
                "neutral": 0,
                "negative": 0,
                "latest_at": (WHEN - timedelta(days=1)).isoformat(),
                "others": True,
            },
        )

    def test_the_colleague_sees_their_own_readable_counts(self):
        self.build()
        body = self.read(self.colleague)
        readable = body["sentiment_readable"]
        # The colleague owns the Hidden account and is the mailbox owner of
        # "Colleague's" — a different readable set than the viewer's own,
        # still missing the engineering ticket neither of them can open.
        self.assertEqual((readable["calls"], readable["emails"], readable["tickets"]), (2, 1, 0))
        self.assertTrue(readable["others"])

    def test_a_manual_sentiment_gives_none(self):
        body = self.read()
        self.assertIsNone(body["sentiment_readable"])

    def test_no_sentiment_evidence_key_anywhere(self):
        import json

        self.build()
        body = self.read()
        self.assertNotIn("sentiment_evidence", json.dumps(body))


class HistoryQueryCountTests(Fixture):
    def test_the_query_count_does_not_grow_with_the_history(self):
        self.client.force_authenticate(self.viewer)
        # The first read resolves the caller's membership and org chart, which
        # are memoised on the user; what is pinned is every read after it.
        self.client.get(self.url)
        for batch in range(2):
            for i in range(3):
                self.call(f"Org {batch}-{i}")
                self.call(f"Seen {batch}-{i}", account=self.seen)
                self.email(f"Mail {batch}-{i}")
                # Emails and tickets sit on an account too, not only a
                # customer, so the `account__customers` prefetch they share
                # with calls is exercised on every kind, not just calls.
                Email.objects.create(
                    account=self.seen,
                    subject=f"Mail Acc {batch}-{i}",
                    sender_name="Sam",
                    recipient_name="Carl",
                    body="Body text.",
                    sent_at=WHEN,
                    from_address="sam@pizzahut.com",
                )
                self.ticket(f"ZD-{batch}-{i}")
                Ticket.objects.create(
                    account=self.seen,
                    ticket_number=f"ZDA-{batch}-{i}",
                    title="Broken export",
                    priority="high",
                    opened_at=date(2026, 9, 19),
                    requester_email="sam@pizzahut.com",
                    external_url="https://acme.zendesk.com/t/1",
                )
            # 12, not 10: with emails and tickets now also on an account
            # (not only a customer), their own `account__customers`
            # prefetch actually has something to fetch — one more query
            # each, over the 10 from calls-on-accounts alone. Flat still
            # means this number holds at both fixture sizes below.
            with self.assertNumQueries(12):
                body = self.client.get(self.url).data
            self.assertEqual(len(body["calls"]), 6 * (batch + 1))
            self.assertEqual(len(body["emails"]), 6 * (batch + 1))
            self.assertEqual(len(body["tickets"]), 6 * (batch + 1))


class HandCorrectionTests(Fixture):
    """A person reading a call the model could not: their correction is a
    reading, so the call is analysed and counts towards the people on it."""

    def test_a_not_analysable_call_corrected_by_hand_reads_as_analysed(self):
        call = self.call("Weekly sync")
        mark_not_analysable(call)
        self.client.force_authenticate(self.viewer)
        response = self.client.patch(
            f"/api/v1/interactions/call/{call.pk}/classification/",
            {"category": "bug_report", "sentiment": "negative"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        call.refresh_from_db()
        self.assertFalse(call.not_analysable)
        row = self.client.get(self.url).data["calls"][0]
        self.assertEqual((row["analysis"], row["sentiment"]), ("analysed", "negative"))
        self.sam.refresh_from_db()
        self.assertEqual(
            (self.sam.sentiment, self.sam.sentiment_source, self.sam.sentiment_evidence["calls"]),
            ("negative", "computed", 1),
        )

    def test_a_hand_reading_of_neutral_still_counts(self):
        """Neutral with no tags is what marking left behind, but a person
        saying so is a reading all the same."""
        call = self.call("Weekly sync")
        mark_not_analysable(call)
        self.client.force_authenticate(self.viewer)
        response = self.client.patch(
            f"/api/v1/interactions/call/{call.pk}/classification/",
            {"sentiment": "neutral"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        call.refresh_from_db()
        self.assertEqual(call.analysis, "analysed")
        self.assertIsNotNone(call.classification_corrected_at)

    def test_a_failed_recompute_does_not_fail_the_saved_correction(self):
        from unittest.mock import patch

        from services.metrics.models import Feedback

        call = self.call("Weekly sync")
        mark_not_analysable(call)
        self.client.force_authenticate(self.viewer)
        with (
            patch(
                "services.customers.contact_sentiment.recompute_for_records",
                side_effect=RuntimeError("boom"),
            ),
            self.assertLogs("services.metrics.feedback", level="WARNING"),
        ):
            response = self.client.patch(
                f"/api/v1/interactions/call/{call.pk}/classification/",
                {"sentiment": "negative"},
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        call.refresh_from_db()
        self.assertEqual((call.analysis, call.sentiment), ("analysed", "negative"))
        self.assertTrue(Feedback.objects.filter(subject_id=call.pk).exists())
