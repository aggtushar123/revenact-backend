"""The person's own inbox: every synced message kept, folders, flags,
categories, the summary block, triage, replies, and that nobody else reads it."""

from datetime import timedelta
from unittest.mock import patch

from core.models import AuditEvent
from services.customers.models import Email
from services.mail import sync
from services.mail.categorise import categorise
from services.mail.models import MailMessage
from services.mail.providers.google import parse_gmail_message
from services.mail.providers.imap import _labels
from services.mail.providers.microsoft import parse_graph_message

from .test_mail import NOW, FakeProvider, Fixture, message


class ProviderLabels(Fixture):
    def test_gmail_labels_and_unsubscribe_header(self):
        full = {
            "id": "abc",
            "threadId": "thr",
            "labelIds": ["INBOX", "UNREAD", "STARRED", "CATEGORY_PROMOTIONS", "Label_9"],
            "internalDate": str(int(NOW.timestamp() * 1000)),
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "From", "value": "Deals <deals@shop.example>"},
                    {"name": "Subject", "value": "40% off"},
                    {"name": "List-Unsubscribe", "value": "<mailto:stop@shop.example>"},
                ],
                "body": {"data": ""},
            },
        }
        m = parse_gmail_message(full)
        self.assertEqual(m.labels, ["inbox", "unread", "starred", "promotions"])
        self.assertEqual(m.headers, {"list-unsubscribe": "<mailto:stop@shop.example>"})

    def test_graph_flags_and_folders(self):
        item = {
            "id": "g1",
            "conversationId": "c1",
            "subject": "Hi",
            "from": {"emailAddress": {"name": "Sam", "address": "sam@pizzahut.com"}},
            "toRecipients": [],
            "sentDateTime": "2026-09-16T09:00:00Z",
            "body": {"contentType": "text", "content": "hello"},
            "isRead": False,
            "flag": {"flagStatus": "flagged"},
            "importance": "high",
            "parentFolderId": "F-INBOX",
        }
        m = parse_graph_message(item, {"F-INBOX": "inbox"})
        self.assertEqual(m.labels, ["inbox", "unread", "starred", "important"])
        draft = parse_graph_message({**item, "isDraft": True}, {"F-INBOX": "inbox"})
        self.assertIn("draft", draft.labels)
        self.assertNotIn("inbox", draft.labels)

    def test_imap_flags(self):
        self.assertEqual(
            _labels("INBOX", b"1 (FLAGS (\\Seen \\Flagged) RFC822 {12}"), ["inbox", "starred"]
        )
        self.assertEqual(
            _labels("[Gmail]/Sent Mail", b"1 (FLAGS () RFC822 {12}"), ["sent", "unread"]
        )

    def test_categories(self):
        self.assertEqual(categorise(message(subject="Invoice #42 is due")), "financial")
        self.assertEqual(categorise(message(subject="Hi", labels=["promotions"])), "promotions")
        self.assertEqual(categorise(message(subject="Hi", labels=["social"])), "social")
        self.assertEqual(
            categorise(
                message(
                    subject="Hi", headers={"List-Unsubscribe": "x"}, from_address="digest@news.io"
                )
            ),
            "newsletters",
        )
        self.assertEqual(categorise(message(subject="Hi", labels=["updates"])), "notifications")
        self.assertEqual(
            categorise(message(subject="Hi", from_address="noreply@github.com")), "notifications"
        )
        self.assertEqual(categorise(message(subject="Hi")), "general")


class OwnInbox(Fixture):
    def setUp(self):
        super().setUp()
        self.boss = self.carl
        self.customer = self.pizza
        self.connection = self.connect(self.dana)
        self.client.force_authenticate(self.dana)

    def _sync(self, *messages):
        FakeProvider.queued = list(messages)
        with patch("services.mail.sync.get_provider", return_value=FakeProvider()):
            return sync.sync_mailbox(self.connection)

    def test_every_message_is_kept_and_matched_ones_link_to_the_filed_copy(self):
        self._sync(
            message(provider_id="m1", labels=["inbox", "unread"]),  # pizzahut → filed
            message(
                provider_id="m2",
                from_address="newsletter@saastr.com",
                from_name="SaaStr",
                subject="This week",
                headers={"List-Unsubscribe": "x"},
                labels=["inbox"],
            ),
            message(
                provider_id="m3", labels=["sent"], from_address="dana@acme.io", to=[("", "x@y.io")]
            ),
        )
        self.assertEqual(MailMessage.objects.count(), 3)
        filed = MailMessage.objects.get(provider_message_id="m1")
        self.assertEqual(filed.email, Email.objects.get())
        self.assertFalse(filed.is_read)
        self.assertTrue(filed.priority)
        unmatched = MailMessage.objects.get(provider_message_id="m2")
        self.assertIsNone(unmatched.email)
        self.assertEqual(unmatched.category, "newsletters")
        self.assertFalse(unmatched.priority)
        self.assertEqual(MailMessage.objects.get(provider_message_id="m3").folder, "sent")
        # A second pass changes nothing.
        self._sync(message(provider_id="m1", labels=["inbox"]))
        self.assertEqual(MailMessage.objects.count(), 3)
        self.assertFalse(MailMessage.objects.get(provider_message_id="m1").is_read)

    def test_list_folders_filters_and_search(self):
        self._sync(
            message(provider_id="a", subject="Renewal terms", labels=["inbox", "unread"]),
            message(
                provider_id="b",
                subject="40% off",
                from_address="deals@shop.io",
                labels=["inbox", "promotions"],
            ),
            message(provider_id="c", subject="Draft", labels=["draft"]),
            message(provider_id="d", subject="Junk", labels=["spam"]),
            message(provider_id="e", subject="Old", labels=["trash"]),
            message(
                provider_id="f",
                subject="Starred",
                from_address="v@ip.io",
                labels=["inbox", "starred", "important"],
            ),
        )
        inbox = self.client.get("/api/v1/mail/messages/").data
        self.assertEqual(
            {r["subject"] for r in inbox["results"]}, {"Renewal terms", "40% off", "Starred"}
        )
        row = next(r for r in inbox["results"] if r["subject"] == "Renewal terms")
        self.assertEqual(
            row["account"], {"id": self.customer.id, "name": "Pizza Hut", "type": "customer"}
        )
        self.assertTrue(row["priority"])
        self.assertNotIn("body", row)
        for folder, expected in (
            ("drafts", {"Draft"}),
            ("spam", {"Junk"}),
            ("trash", {"Old"}),
            ("starred", {"Starred"}),
            ("important", {"Starred"}),
            ("sent", set()),
        ):
            got = self.client.get(f"/api/v1/mail/messages/?folder={folder}").data["results"]
            self.assertEqual({r["subject"] for r in got}, expected, folder)
        print(
            [
                (r["subject"], r["is_read"])
                for r in self.client.get("/api/v1/mail/messages/?unread=true").data["results"]
            ],
            list(MailMessage.objects.values_list("subject", "is_read")),
        )
        self.assertEqual(
            [
                r["subject"]
                for r in self.client.get("/api/v1/mail/messages/?unread=true").data["results"]
            ],
            ["Renewal terms"],
        )
        self.assertEqual(
            {
                r["subject"]
                for r in self.client.get("/api/v1/mail/messages/?priority=true").data["results"]
            },
            {"Renewal terms", "Starred"},
        )
        self.assertEqual(
            [
                r["subject"]
                for r in self.client.get("/api/v1/mail/messages/?category=promotions").data[
                    "results"
                ]
            ],
            ["40% off"],
        )
        self.assertEqual(
            [
                r["subject"]
                for r in self.client.get("/api/v1/mail/messages/?q=terms").data["results"]
            ],
            ["Renewal terms"],
        )
        self.assertEqual(self.client.get("/api/v1/mail/messages/?folder=nope").status_code, 400)

    def test_summary_counts_and_categories_block(self):
        self._sync(
            message(
                provider_id="a",
                subject="Invoice 12",
                from_name="Circleback",
                labels=["inbox", "unread"],
                date=NOW - timedelta(hours=1),
            ),
            message(
                provider_id="b",
                subject="Invoice 13",
                from_name="Stripe",
                labels=["inbox", "unread"],
            ),
            message(
                provider_id="c",
                subject="Security alert",
                from_address="no-reply@google.com",
                from_name="Google",
                labels=["inbox", "unread"],
            ),
            message(provider_id="d", subject="Read already", labels=["inbox"]),
            message(provider_id="e", subject="Sent one", labels=["sent"]),
        )
        summary = self.client.get("/api/v1/mail/messages/summary/").data
        self.assertTrue(summary["has_mailbox"])
        self.assertEqual(
            summary["folders"], {"inbox": 4, "drafts": 0, "sent": 1, "done": 0, "muted": 0}
        )
        self.assertEqual(summary["unread"], 3)
        self.assertEqual(
            summary["categories"],
            [
                {
                    "category": "financial",
                    "label": "Financial",
                    "count": 2,
                    "subjects": ["Invoice 13", "Invoice 12"],
                    "senders": ["Stripe"],
                    "more_senders": 1,
                },
                {
                    "category": "notifications",
                    "label": "Notifications",
                    "count": 1,
                    "subjects": ["Security alert"],
                    "senders": ["Google"],
                    "more_senders": 0,
                },
            ],
        )

    def test_detail_and_triage(self):
        self._sync(message(provider_id="a", labels=["inbox", "unread"]))
        row = MailMessage.objects.get()
        detail = self.client.get(f"/api/v1/mail/messages/{row.id}/").data
        self.assertEqual(detail["body"], "Can we talk about the renewal?")
        self.client.patch(
            f"/api/v1/mail/messages/{row.id}/", {"is_read": True, "state": "done"}, format="json"
        )
        self.assertEqual(self.client.get("/api/v1/mail/messages/").data["count"], 0)
        self.assertEqual(self.client.get("/api/v1/mail/messages/?folder=done").data["count"], 1)
        self.client.patch(
            f"/api/v1/mail/messages/{row.id}/",
            {"state": "muted", "is_starred": True},
            format="json",
        )
        self.assertEqual(self.client.get("/api/v1/mail/messages/?folder=muted").data["count"], 1)
        self.assertEqual(self.client.get("/api/v1/mail/messages/?folder=starred").data["count"], 1)
        bad = self.client.patch(
            f"/api/v1/mail/messages/{row.id}/", {"state": "gone"}, format="json"
        )
        self.assertEqual(bad.status_code, 400)

    def test_nobody_else_reads_it_not_even_the_manager(self):
        self._sync(message(provider_id="a", labels=["inbox"]))
        row = MailMessage.objects.get()
        self.client.force_authenticate(self.boss)
        self.assertEqual(self.client.get("/api/v1/mail/messages/").data["count"], 0)
        self.assertEqual(self.client.get(f"/api/v1/mail/messages/{row.id}/").status_code, 404)
        self.assertEqual(
            self.client.get("/api/v1/mail/messages/summary/").data["has_mailbox"], False
        )

    def test_reply_goes_out_lands_in_sent_and_is_filed_when_the_original_was(self):
        self._sync(
            message(provider_id="a", labels=["inbox", "unread"]),
            message(
                provider_id="b",
                from_address="stranger@else.io",
                from_name="S",
                subject="Hey",
                labels=["inbox"],
            ),
        )
        filed = MailMessage.objects.get(provider_message_id="a")
        with patch("services.mail.sync.get_provider", return_value=FakeProvider()):
            response = self.client.post(
                f"/api/v1/mail/messages/{filed.id}/reply/",
                {"body": "Yes, Thursday."},
                format="json",
            )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(
            FakeProvider.sent[-1][1:], (["sam@pizzahut.com"], "Re: Renewal", "Yes, Thursday.")
        )
        self.assertEqual(response.data["folder"], "sent")
        self.assertEqual(response.data["direction"], "sent")
        self.assertEqual(response.data["account"]["name"], "Pizza Hut")
        self.assertEqual(Email.objects.filter(direction="sent", customer=self.customer).count(), 1)
        filed.refresh_from_db()
        self.assertTrue(filed.is_read)
        self.assertTrue(AuditEvent.objects.filter(action="mailbox.reply", actor=self.dana).exists())

        stranger = MailMessage.objects.get(provider_message_id="b")
        FakeProvider.sent.clear()

        class SecondSend(FakeProvider):
            def send(self, creds, **kw):
                sent = super().send(creds, **kw)
                sent.provider_id = "sent-2"
                return sent

        with patch("services.mail.sync.get_provider", return_value=SecondSend()):
            response = self.client.post(
                f"/api/v1/mail/messages/{stranger.id}/reply/", {"body": "Hello."}, format="json"
            )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(FakeProvider.sent[-1][1], ["stranger@else.io"])
        self.assertIsNone(response.data["account"])
        self.assertEqual(
            Email.objects.filter(direction="sent").count(), 1
        )  # not filed: nobody in the book
        self.assertEqual(self.client.get("/api/v1/mail/messages/?folder=sent").data["count"], 2)

    def test_disconnecting_takes_the_personal_copy_away_and_keeps_the_filed_one(self):
        self._sync(message(provider_id="a", labels=["inbox"]))
        self.assertEqual(self.client.delete("/api/v1/mail/connection/").status_code, 204)
        self.assertEqual(MailMessage.objects.count(), 0)
        self.assertEqual(Email.objects.count(), 1)

    def test_sync_keeps_a_copy_of_what_the_person_sends_through_the_app(self):
        with patch("services.mail.sync.get_provider", return_value=FakeProvider()):
            response = self.client.post(
                f"/api/v1/customers/{self.customer.id}/emails/send/",
                {"to": ["sam@pizzahut.com"], "subject": "Terms", "body": "Attached."},
                format="json",
            )
        self.assertEqual(response.status_code, 201, response.data)
        sent = MailMessage.objects.get()
        self.assertEqual(
            (sent.folder, sent.direction, sent.email_id), ("sent", "sent", Email.objects.get().id)
        )
        self.assertGreater(sent.sent_at, NOW - timedelta(days=1))
