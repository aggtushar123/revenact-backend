"""End-to-end tier: real server, real HTTP, real test DB. A CSM asks Ask
Revenact from the Accounts Board and from one account's page about a note;
the history tags each conversation with where it was asked. The admin,
mentioned, reads the question but not the reply that quoted the CSM's own
note. A peer who cannot open the account cannot ask about it, and an item
that does not exist is refused the same way as a real item on the same
account the asker may not read. The model call is stubbed in-process."""

from datetime import timedelta
from unittest.mock import patch

from django.test import LiveServerTestCase
from django.utils import timezone

from e2e.http import http_get, http_post


class AccountsAskFlowTests(LiveServerTestCase):
    def api(self, path):
        return f"{self.live_server_url}/api/v1{path}"

    def add_user(self, admin, name, email, password):
        status, body = http_post(
            self.api("/auth/users/"),
            {"name": name, "email": email, "password": password},
            token=admin,
        )
        self.assertEqual(status, 201, body)
        status, body = http_post(self.api("/auth/login/"), {"email": email, "password": password})
        self.assertEqual(status, 200, body)
        return body["access"]

    def created(self, path, payload, token):
        status, body = http_post(self.api(path), payload, token=token)
        self.assertEqual(status, 201, body)
        return body

    def ask(self, context, token, content="What is going on?"):
        return http_post(
            self.api("/copilot/messages/"), {"content": content, "context": context}, token=token
        )

    def test_full_flow(self):
        today = timezone.localdate()

        # 1. An organisation signs up; its admin adds two CSMs.
        status, body = http_post(
            self.api("/auth/signup/"),
            {
                "organisation_name": "Acme Inc",
                "name": "Alice Admin",
                "email": "alice@acme.io",
                "password": "supersecret1",
            },
        )
        self.assertEqual(status, 201, body)
        admin = body["access"]
        carl = self.add_user(admin, "Carl CSM", "carl@acme.io", "csmpassword1")
        dana = self.add_user(admin, "Dana CSM", "dana@acme.io", "csmpassword2")

        # 2. Carl's organisation, its EMEA account renewing in ten days, and a
        #    note filed on the account page.
        pizza = self.created("/customers/", {"name": "Pizza Hut"}, carl)["id"]
        emea = self.created(
            f"/customers/{pizza}/accounts/",
            {"name": "EMEA", "renewal_date": (today + timedelta(days=10)).isoformat()},
            carl,
        )["id"]
        note = self.created(
            f"/accounts/{emea}/notes/",
            {"title": "Champion left", "body": "Sam moved on to Globex."},
            carl,
        )["id"]

        with patch(
            "services.copilot.views.get_completion", return_value="Sam's exit is the risk."
        ) as completion:
            # 3. Carl asks from the Board, renewals within 30 days.
            status, body = self.ask(
                {"surface": "accounts", "view": "board", "filters": {"renews_within": "30"}},
                carl,
                "What renews soon?",
            )
            self.assertEqual(status, 200, body)
            system = completion.call_args.kwargs["system"]
            self.assertEqual(completion.call_args.kwargs["purpose"], "accounts")
            self.assertIn("Accounts data:\n<dashboard_data>", system)
            self.assertIn("Screen: Accounts › Board", system)
            self.assertIn("  - EMEA (Pizza Hut): renews ", system)
            board_origin = {
                "surface": "accounts",
                "view": "board",
                "filters": {"renews_within": "30"},
                "label": "Accounts · Renews within 30 days",
            }
            self.assertEqual(body["origin"], board_origin)

            # 4. Carl asks about the note from EMEA's page, mentioning Alice.
            status, body = self.ask(
                {
                    "surface": "accounts",
                    "view": "detail",
                    "account": emea,
                    "focus": {"kind": "note", "id": note},
                },
                carl,
                "@Alice Admin, what does this mean for EMEA?",
            )
            self.assertEqual(status, 200, body)
            conversation = body["id"]
            system = completion.call_args.kwargs["system"]
            self.assertIn("Account page data:\n<dashboard_data>", system)
            self.assertIn("Screen: Accounts › EMEA (one account's page)", system)
            self.assertIn("The asker is asking about this story item:", system)
            self.assertIn("Champion left", system)
            page_origin = {
                "surface": "accounts",
                "view": "detail",
                "account": emea,
                "label": "EMEA",
            }
            self.assertEqual(body["origin"], page_origin)

            # 5. The history tags both conversations with where they were asked.
            status, listed = http_get(self.api("/copilot/conversations/"), token=carl)
            self.assertEqual(status, 200, listed)
            self.assertCountEqual([c["origin"] for c in listed], [board_origin, page_origin])

            # 6. Alice, mentioned, reads her question but not the reply: it
            #    quoted Carl's own note, which only he and his chain may read.
            status, body = http_get(
                self.api(f"/copilot/conversations/{conversation}/"), token=admin
            )
            self.assertEqual(status, 200, body)
            self.assertEqual(body["visibility"], "partial")
            self.assertIn("Alice Admin", body["messages"][0]["content"])
            self.assertIn("isn't shared with you", body["messages"][1]["content"])

            # 7. Dana cannot open Carl's account, so she cannot ask about it.
            calls = completion.call_count
            status, body = self.ask(
                {"surface": "accounts", "view": "detail", "account": emea}, dana
            )
            self.assertEqual(status, 400, body)
            self.assertEqual(body, {"context": {"account": ["Not an account you can open."]}})
            self.assertEqual(completion.call_count, calls)

            # 8. An item that does not exist reads exactly like a real one the
            #    asker may not read: Alice, the admin, sees every account
            #    (Admin holds view_all_accounts) but — as step 6 already
            #    showed — cannot read Carl's own note, which only he and his
            #    management chain may read. Her 400 on the real note is
            #    byte-identical to Carl's 400 on an id that never existed.
            status, missing_body = self.ask(
                {
                    "surface": "accounts",
                    "view": "detail",
                    "account": emea,
                    "focus": {"kind": "note", "id": 999999},
                },
                carl,
            )
            self.assertEqual(status, 400, missing_body)
            self.assertEqual(
                missing_body, {"context": {"focus": ["Not a story item you can open."]}}
            )
            status, unreadable_body = self.ask(
                {
                    "surface": "accounts",
                    "view": "detail",
                    "account": emea,
                    "focus": {"kind": "note", "id": note},
                },
                admin,
            )
            self.assertEqual(status, 400, unreadable_body)
            self.assertEqual(unreadable_body, missing_body)
            self.assertEqual(completion.call_count, calls)
