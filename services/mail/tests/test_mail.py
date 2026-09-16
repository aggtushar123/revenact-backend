"""Personal mailboxes: connect, sync, send, and who may read what."""

from datetime import datetime
from datetime import timezone as dt_timezone
from unittest.mock import patch

from django.core.management import call_command
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import AuditEvent
from services.accounts.models import Organisation, User
from services.customers.models import Account, Contact, Customer, Email
from services.mail import sync
from services.mail.models import MailboxConnection
from services.mail.providers.base import Credentials, Message, ProviderError
from services.mail.providers.google import parse_gmail_message
from services.mail.providers.imap import parse_rfc822
from services.mail.providers.microsoft import parse_graph_message

NOW = datetime(2026, 9, 16, 9, 0, tzinfo=dt_timezone.utc)


def message(**overrides):
    base = dict(
        provider_id="m1",
        thread_id="t1",
        subject="Renewal",
        from_address="sam@pizzahut.com",
        from_name="Sam Pizza",
        to=[("Dana", "dana@acme.io")],
        date=NOW,
        body="Can we talk about the renewal?",
    )
    base.update(overrides)
    return Message(**base)


class FakeProvider:
    """A provider that hands back whatever the test queued, and records sends."""

    key = "imap"
    label = "Fake"
    uses_oauth = False
    queued = []
    sent = []

    @classmethod
    def configured(cls):
        return True

    def fetch_messages(self, creds, cursor):
        return list(self.queued), "cursor-2", creds

    def send(self, creds, *, to, subject, body):
        self.sent.append((creds.address, to, subject, body))
        return message(
            provider_id="sent-1",
            thread_id="t-sent",
            from_address=creds.address,
            to=[("", a) for a in to],
            subject=subject,
            body=body,
        )


class Fixture(APITestCase):
    def setUp(self):
        FakeProvider.queued, FakeProvider.sent = [], []
        # Never a live model call from a test: filing mail classifies it, and
        # the developer's own key must not be spent. Tests that want an
        # answer patch the same target again, closer in.
        from services.copilot.anthropic_client import CopilotNotConfigured

        no_model = patch(
            "services.customers.classification.get_completion",
            side_effect=CopilotNotConfigured("no model in tests"),
        )
        no_model.start()
        self.addCleanup(no_model.stop)
        self.org = Organisation.objects.create(name="Acme Inc")
        mk = lambda email, name, **kw: User.objects.create_user(  # noqa: E731
            email=email, password="x", name=name, organisation=self.org, **kw
        )
        self.alice = mk("alice@acme.io", "Alice", role=User.Role.ADMIN)
        self.carl = mk("carl@acme.io", "Carl", reports_to=self.alice)
        self.dana = mk("dana@acme.io", "Dana", reports_to=self.carl)
        self.priya = mk("priya@acme.io", "Priya", reports_to=self.alice)
        # Everyone may open the customer; mail visibility is the rule under test.
        from services.accounts.capabilities import Capability
        from services.accounts.models import Role

        lead = Role.objects.create(
            organisation=self.org,
            name="Lead",
            slug="lead",
            permissions=[Capability.VIEW_ALL_ACCOUNTS],
        )
        User.objects.filter(pk__in=[self.carl.pk, self.dana.pk, self.priya.pk]).update(role=lead)
        for person in (self.carl, self.dana, self.priya):
            person.refresh_from_db()
        self.pizza = Customer.objects.create(
            organisation=self.org, name="Pizza Hut", domain="pizzahut.com", owner=self.carl
        )
        self.apac = Account.objects.create(name="APAC", domain="apac.pizzahut.com")
        self.apac.customers.add(self.pizza)

    def connect(self, user, address=None):
        connection = MailboxConnection.objects.create(
            organisation=self.org,
            user=user,
            provider="imap",
            address=address or user.email,
            display_name=user.name,
            credentials="",
        )
        connection.set_credentials({"password": "app-pass", "imap_host": "imap.acme.io"})
        connection.save()
        return connection

    def email(self, owner, direction="received", **kw):
        return Email.objects.create(
            customer=self.pizza,
            subject=kw.pop("subject", f"{owner.name if owner else 'legacy'} mail"),
            sender_name="x",
            recipient_name="y",
            body=kw.pop("body", "hello"),
            sent_at=NOW,
            mailbox_owner=owner,
            direction=direction if owner else "",
            **kw,
        )


class VisibilityTests(Fixture):
    def test_you_see_yours_and_your_reports_never_your_seniors_or_peers(self):
        legacy = self.email(None)
        dana_mail = self.email(self.dana)
        carl_mail = self.email(self.carl)
        alice_mail = self.email(self.alice)
        priya_mail = self.email(self.priya)
        url = f"/api/v1/customers/{self.pizza.id}/emails/"

        def seen_by(user):
            self.client.force_authenticate(user)
            return {row["subject"] for row in self.client.get(url).data}

        self.assertEqual(seen_by(self.dana), {legacy.subject, dana_mail.subject})
        self.assertEqual(seen_by(self.carl), {legacy.subject, dana_mail.subject, carl_mail.subject})
        self.assertEqual(
            seen_by(self.alice),
            {
                legacy.subject,
                dana_mail.subject,
                carl_mail.subject,
                alice_mail.subject,
                priya_mail.subject,
            },
        )
        self.assertEqual(seen_by(self.priya), {legacy.subject, priya_mail.subject})

    def test_the_serializer_says_whose_it_is_and_which_way_it_went(self):
        self.email(
            self.dana,
            direction="sent",
            from_address="dana@acme.io",
            to_addresses=["sam@pizzahut.com"],
        )
        self.client.force_authenticate(self.dana)
        row = self.client.get(f"/api/v1/customers/{self.pizza.id}/emails/").data[0]
        self.assertEqual(row["direction"], "sent")
        self.assertEqual(row["mailbox_owner"], {"id": self.dana.id, "name": "Dana"})
        self.assertEqual(row["to_addresses"], ["sam@pizzahut.com"])


class SyncTests(Fixture):
    def test_files_what_belongs_to_the_book_and_ignores_the_rest_once(self):
        connection = self.connect(self.dana)
        Contact.objects.create(customer=self.pizza, name="Kim", email="kim@example.org")
        FakeProvider.queued = [
            message(provider_id="a", from_address="sam@pizzahut.com"),  # customer by domain
            message(provider_id="b", from_address="ops@apac.pizzahut.com"),  # account by domain
            message(provider_id="c", from_address="kim@example.org"),  # customer by contact
            message(provider_id="d", from_address="nobody@elsewhere.com"),  # not in the book
            message(  # sent by Dana: counterpart is the recipient
                provider_id="e",
                from_address="dana@acme.io",
                from_name="Dana",
                to=[("Sam Pizza", "sam@pizzahut.com")],
                subject="Following up",
            ),
        ]
        with patch("services.mail.sync.get_provider", return_value=FakeProvider()):
            filed = sync.sync_mailbox(connection)
            again = sync.sync_mailbox(connection)
        self.assertEqual((filed, again), (4, 0))
        by = {e.provider_message_id: e for e in Email.objects.filter(mailbox=connection)}
        self.assertEqual(set(by), {"a", "b", "c", "e"})
        self.assertEqual(by["a"].customer, self.pizza)
        self.assertEqual(by["b"].account, self.apac)
        self.assertEqual(by["c"].customer, self.pizza)
        self.assertEqual((by["a"].direction, by["e"].direction), ("received", "sent"))
        self.assertEqual(by["e"].sender_name, "Dana")
        self.assertEqual(by["e"].recipient_name, "Sam Pizza")
        self.assertEqual(by["a"].mailbox_owner, self.dana)
        connection.refresh_from_db()
        self.assertEqual(connection.sync_cursor, "cursor-2")
        self.assertEqual(connection.status, "connected")

    def test_a_provider_that_needs_reauth_marks_the_connection_not_the_emails(self):
        connection = self.connect(self.dana)

        class Broken(FakeProvider):
            def fetch_messages(self, creds, cursor):
                raise ProviderError("token revoked", reauth=True)

        with patch("services.mail.sync.get_provider", return_value=Broken()):
            self.assertEqual(sync.sync_mailbox(connection), 0)
        connection.refresh_from_db()
        self.assertEqual(connection.status, "error")
        self.assertIn("token revoked", connection.error)

    def test_the_command_syncs_every_connection(self):
        self.connect(self.dana)
        self.connect(self.carl)
        FakeProvider.queued = [message(provider_id="z")]
        with patch("services.mail.sync.get_provider", return_value=FakeProvider()):
            call_command("sync_mail", verbosity=0)
        self.assertEqual(Email.objects.filter(provider_message_id="z").count(), 2)


class ConnectionTests(Fixture):
    def test_credentials_are_encrypted_at_rest_and_never_serialised(self):
        connection = self.connect(self.dana)
        raw = MailboxConnection.objects.filter(pk=connection.pk).values_list(
            "credentials", flat=True
        )[0]
        self.assertNotIn("app-pass", raw)
        self.assertEqual(connection.get_credentials()["password"], "app-pass")
        self.client.force_authenticate(self.dana)
        data = self.client.get("/api/v1/mail/connection/").data
        self.assertEqual(data["connection"]["address"], "dana@acme.io")
        self.assertNotIn("credentials", data["connection"])
        self.assertIn(
            {"key": "imap", "label": "IMAP / SMTP", "uses_oauth": False}, data["providers"]
        )

    def test_imap_connect_verifies_the_login_stores_it_and_is_audited(self):
        self.client.force_authenticate(self.dana)
        with patch("services.mail.providers.imap.ImapProvider._imap") as login:
            login.return_value.__enter__ = lambda s: s
            login.return_value.__exit__ = lambda s, *a: None
            response = self.client.post(
                "/api/v1/mail/connect/imap/",
                {
                    "address": "Dana@Acme.io",
                    "password": "app-pass",
                    "imap_host": "imap.acme.io",
                    "smtp_host": "smtp.acme.io",
                },
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["address"], "dana@acme.io")
        connection = MailboxConnection.objects.get(user=self.dana)
        self.assertEqual(connection.get_credentials()["smtp_port"], 587)
        event = AuditEvent.objects.get(action="mailbox.connect")
        self.assertEqual(event.actor, self.dana)
        self.assertNotIn("app-pass", str(event.metadata))
        # Disconnecting destroys the credentials and is written down too.
        self.assertEqual(self.client.delete("/api/v1/mail/connection/").status_code, 204)
        self.assertFalse(MailboxConnection.objects.filter(user=self.dana).exists())
        self.assertTrue(AuditEvent.objects.filter(action="mailbox.disconnect").exists())

    def test_imap_connect_refuses_a_login_that_fails(self):
        self.client.force_authenticate(self.dana)
        with patch(
            "services.mail.providers.imap.ImapProvider._imap",
            side_effect=ProviderError("IMAP login failed: bad password", reauth=True),
        ):
            response = self.client.post(
                "/api/v1/mail/connect/imap/",
                {
                    "address": "dana@acme.io",
                    "password": "x",
                    "imap_host": "imap.acme.io",
                    "smtp_host": "smtp.acme.io",
                },
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(MailboxConnection.objects.exists())

    def test_oauth_providers_are_offered_only_when_configured(self):
        self.client.force_authenticate(self.dana)
        keys = {p["key"] for p in self.client.get("/api/v1/mail/connection/").data["providers"]}
        self.assertEqual(keys, {"imap"})
        response = self.client.post("/api/v1/mail/connect/google/")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @override_settings(
        GOOGLE_OAUTH_CLIENT_ID="cid",
        GOOGLE_OAUTH_CLIENT_SECRET="sec",
        FRONTEND_URL="https://app.test",
    )
    def test_the_oauth_round_trip_trusts_only_the_signed_state(self):
        self.client.force_authenticate(self.dana)
        start = self.client.post("/api/v1/mail/connect/google/")
        self.assertEqual(start.status_code, 200)
        url = start.data["authorize_url"]
        self.assertIn("accounts.google.com", url)
        from urllib.parse import parse_qs, urlsplit

        state = parse_qs(urlsplit(url).query)["state"][0]
        self.assertTrue(
            parse_qs(urlsplit(url).query)["redirect_uri"][0].endswith(
                "/api/v1/mail/oauth/google/callback/"
            )
        )

        self.client.force_authenticate(None)
        with patch(
            "services.mail.providers.google.GoogleProvider.exchange_code",
            return_value=Credentials(
                address="dana@acme.io",
                display_name="Dana",
                data={"refresh_token": "r", "access_token": "a", "expires_at": 9e12},
            ),
        ):
            done = self.client.get(f"/api/v1/mail/oauth/google/callback/?code=xyz&state={state}")
        self.assertEqual(done.status_code, 302)
        self.assertEqual(done["Location"], "https://app.test/integrations?mailbox=connected")
        connection = MailboxConnection.objects.get(user=self.dana)
        self.assertEqual((connection.provider, connection.address), ("google", "dana@acme.io"))
        self.assertEqual(connection.get_credentials()["refresh_token"], "r")

        bad = self.client.get("/api/v1/mail/oauth/google/callback/?code=xyz&state=forged")
        self.assertEqual(bad.status_code, 302)
        self.assertIn("mailbox=error", bad["Location"])


class ComposeTests(Fixture):
    def test_sends_through_my_mailbox_and_files_the_copy(self):
        self.connect(self.dana)
        self.client.force_authenticate(self.dana)
        with patch("services.mail.sync.get_provider", return_value=FakeProvider()):
            response = self.client.post(
                f"/api/v1/customers/{self.pizza.id}/emails/send/",
                {"to": ["sam@pizzahut.com"], "subject": "Renewal", "body": "Shall we?"},
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(
            FakeProvider.sent, [("dana@acme.io", ["sam@pizzahut.com"], "Renewal", "Shall we?")]
        )
        self.assertEqual(response.data["direction"], "sent")
        self.assertEqual(response.data["mailbox_owner"]["name"], "Dana")
        email = Email.objects.get(provider_message_id="sent-1")
        self.assertEqual((email.customer, email.mailbox_owner), (self.pizza, self.dana))
        # Carl (her manager) sees it; Priya (a peer) does not.
        self.client.force_authenticate(self.carl)
        self.assertEqual(len(self.client.get(f"/api/v1/customers/{self.pizza.id}/emails/").data), 1)
        self.client.force_authenticate(self.priya)
        self.assertEqual(len(self.client.get(f"/api/v1/customers/{self.pizza.id}/emails/").data), 0)

    def test_needs_a_connected_mailbox(self):
        self.client.force_authenticate(self.dana)
        response = self.client.post(
            f"/api/v1/customers/{self.pizza.id}/emails/send/",
            {"to": ["sam@pizzahut.com"], "subject": "Hi", "body": "x"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Connect your mailbox", response.data["detail"])


class CopilotScopeTests(Fixture):
    def test_retrieval_and_replies_follow_the_same_rule(self):
        from services.copilot.models import Message as Turn
        from services.copilot.retrieval import _gather_candidates
        from services.copilot.views import _reply_readable_by

        carl_mail = self.email(
            self.carl, subject="Carl's private thread", body="pricing concession"
        )
        dana_mail = self.email(self.dana, subject="Dana's thread", body="onboarding")
        lines_for_dana = [item.line for item in _gather_candidates(self.pizza, viewer=self.dana)]
        self.assertTrue(any("Dana's thread" in line for line in lines_for_dana))
        self.assertFalse(any("Carl's private thread" in line for line in lines_for_dana))
        lines_for_carl = [item.line for item in _gather_candidates(self.pizza, viewer=self.carl)]
        self.assertTrue(any("Carl's private thread" in line for line in lines_for_carl))

        turn = Turn(
            sources=[
                {
                    "type": "email",
                    "id": carl_mail.id,
                    "company_type": "customer",
                    "company_id": self.pizza.id,
                }
            ]
        )
        self.assertFalse(_reply_readable_by(turn, self.dana))
        self.assertTrue(_reply_readable_by(turn, self.carl))
        self.assertTrue(_reply_readable_by(turn, self.alice))
        turn = Turn(
            sources=[
                {
                    "type": "email",
                    "id": dana_mail.id,
                    "company_type": "customer",
                    "company_id": self.pizza.id,
                }
            ]
        )
        self.assertTrue(_reply_readable_by(turn, self.dana))


class ParserTests(APITestCase):
    def test_gmail_payload(self):
        import base64

        body = base64.urlsafe_b64encode(b"Hello there").decode().rstrip("=")
        full = {
            "id": "abc",
            "threadId": "thr",
            "internalDate": str(int(NOW.timestamp() * 1000)),
            "payload": {
                "mimeType": "multipart/alternative",
                "headers": [
                    {"name": "From", "value": "Sam Pizza <Sam@PizzaHut.com>"},
                    {"name": "To", "value": "dana@acme.io, Carl <carl@acme.io>"},
                    {"name": "Subject", "value": "Renewal"},
                ],
                "parts": [{"mimeType": "text/plain", "body": {"data": body}}],
            },
        }
        m = parse_gmail_message(full)
        self.assertEqual((m.provider_id, m.thread_id, m.subject), ("abc", "thr", "Renewal"))
        self.assertEqual((m.from_name, m.from_address), ("Sam Pizza", "sam@pizzahut.com"))
        self.assertEqual(m.to, [("", "dana@acme.io"), ("Carl", "carl@acme.io")])
        self.assertEqual((m.body, m.date), ("Hello there", NOW))

    def test_graph_item(self):
        item = {
            "id": "g1",
            "conversationId": "c1",
            "subject": "Hi",
            "from": {"emailAddress": {"name": "Sam", "address": "SAM@pizzahut.com"}},
            "toRecipients": [{"emailAddress": {"name": "Dana", "address": "dana@acme.io"}}],
            "ccRecipients": [],
            "sentDateTime": "2026-09-16T09:00:00Z",
            "body": {"contentType": "html", "content": "<p>Hello <b>there</b></p>"},
        }
        m = parse_graph_message(item)
        self.assertEqual(
            (m.from_address, m.to, m.body, m.date),
            ("sam@pizzahut.com", [("Dana", "dana@acme.io")], "Hello there", NOW),
        )

    def test_rfc822(self):
        raw = (
            b"From: Sam Pizza <sam@pizzahut.com>\r\nTo: dana@acme.io\r\nSubject: Renewal\r\n"
            b"Date: Wed, 16 Sep 2026 09:00:00 +0000\r\nMessage-ID: <m1@pizzahut.com>\r\n"
            b"Content-Type: text/plain\r\n\r\nHello there\r\n"
        )
        m = parse_rfc822(raw)
        self.assertEqual(
            (m.provider_id, m.subject, m.from_address, m.date),
            ("<m1@pizzahut.com>", "Renewal", "sam@pizzahut.com", NOW),
        )
        self.assertEqual(m.body.strip(), "Hello there")
        self.assertEqual(m.to, [("", "dana@acme.io")])


class HttpGuardTests(APITestCase):
    def test_providers_may_only_call_their_own_hosts_over_https(self):
        from services.mail.providers.base import http_json

        with self.assertRaises(ProviderError):
            http_json("GET", "https://evil.example.com/token")
        with self.assertRaises(ProviderError):
            http_json("GET", "http://graph.microsoft.com/v1.0/me")


class SentimentOnSyncTests(Fixture):
    """Synced and sent mail is classified on the spot, so the pulse can count it."""

    def _answer(self, batch, **_kw):
        import json

        return json.dumps(
            [
                {"ref": f"email:{r.pk}", "category": "bug_report", "sentiment": "negative"}
                for r in batch
            ]
        )

    def test_synced_emails_get_a_sentiment_and_the_pulse_counts_them(self):
        connection = self.connect(self.dana)
        FakeProvider.queued = [message(provider_id="s1", from_address="sam@pizzahut.com")]
        with (
            patch("services.mail.sync.get_provider", return_value=FakeProvider()),
            patch(
                "services.customers.classification.get_completion",
                side_effect=lambda **kw: self._answer(self._batch_from(kw)),
            ),
        ):
            sync.sync_mailbox(connection)
        email = Email.objects.get(provider_message_id="s1")
        self.assertEqual(email.sentiment, "negative")
        self.assertIsNotNone(email.ai_classified_at)
        by = {r.key: r for r in self.pizza.account_pulse().readings}
        self.assertEqual(by["sentiment"].note, "0 positive, 1 negative of 1 in the last 30 days")

    @staticmethod
    def _batch_from(kwargs):
        """The records a prompt was built from: their refs are in the user message."""
        import re

        text = kwargs["messages"][0]["content"]
        ids = [int(pk) for pk in re.findall(r"email:(\d+)", text)]
        return list(Email.objects.filter(pk__in=ids))

    def test_a_sent_email_is_classified_too_and_a_missing_key_is_not_fatal(self):
        self.connect(self.dana)
        self.client.force_authenticate(self.dana)
        with (
            patch("services.mail.sync.get_provider", return_value=FakeProvider()),
            patch(
                "services.customers.classification.get_completion",
                side_effect=lambda **kw: self._answer(self._batch_from(kw)),
            ),
        ):
            response = self.client.post(
                f"/api/v1/customers/{self.pizza.id}/emails/send/",
                {"to": ["sam@pizzahut.com"], "subject": "Renewal", "body": "Shall we?"},
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Email.objects.get(provider_message_id="sent-1").sentiment, "negative")
        # No model configured: the sync still files the mail, unclassified.
        from services.copilot.anthropic_client import CopilotNotConfigured

        FakeProvider.queued = [message(provider_id="s2", from_address="sam@pizzahut.com")]
        with (
            patch("services.mail.sync.get_provider", return_value=FakeProvider()),
            patch(
                "services.customers.classification.get_completion",
                side_effect=CopilotNotConfigured("no key"),
            ),
        ):
            filed = sync.sync_mailbox(self.dana.mailbox)
        self.assertEqual(filed, 1)
        self.assertIsNone(Email.objects.get(provider_message_id="s2").ai_classified_at)
