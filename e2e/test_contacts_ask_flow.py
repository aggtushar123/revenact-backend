"""End-to-end tier: real server, real HTTP, real test DB. A CSM asks Ask
Revenact about one person on Contacts, then about his filtered list; the
history tags each conversation with where it was asked. A peer who cannot
open the person's account cannot ask about them. The model call is stubbed
in-process."""

from unittest.mock import patch

from django.test import LiveServerTestCase

from e2e.http import http_get, http_post


class ContactsAskFlowTests(LiveServerTestCase):
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

    @patch("services.copilot.views.get_completion", return_value="Sam sounds unhappy.")
    def test_full_flow(self, completion):
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

        # 2. Carl's organisation and one person on it.
        status, body = http_post(self.api("/customers/"), {"name": "Pizza Hut"}, token=carl)
        self.assertEqual(status, 201, body)
        pizza = body["id"]
        status, body = http_post(
            self.api(f"/customers/{pizza}/contacts/"),
            {
                "name": "Sam Pizza",
                "email": "sam@pizzahut.com",
                "role": "decision_maker",
                "sentiment": "negative",
            },
            token=carl,
        )
        self.assertEqual(status, 201, body)
        sam = body["id"]

        # 3. Carl asks about Sam from his profile.
        status, body = http_post(
            self.api("/copilot/messages/"),
            {
                "content": "Why is Sam negative?",
                "context": {
                    "surface": "contacts",
                    "view": "person",
                    "contact": sam,
                    "focus": "sentiment",
                },
            },
            token=carl,
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["origin"]["label"], "Sam Pizza · Pizza Hut")
        self.assertIn("set by hand", completion.call_args.kwargs["system"])

        # 4. Carl asks about his negative people from the list.
        status, body = http_post(
            self.api("/copilot/messages/"),
            {
                "content": "Who is unhappy?",
                "context": {
                    "surface": "contacts",
                    "view": "list",
                    "filters": {"sentiment": "negative"},
                },
            },
            token=carl,
        )
        self.assertEqual(status, 200, body)
        status, listed = http_get(self.api("/copilot/conversations/"), token=carl)
        self.assertEqual(status, 200, listed)
        self.assertEqual(
            {c["origin"]["label"] for c in listed},
            {"Sam Pizza · Pizza Hut", "Contacts · Negative"},
        )

        # 5. Dana cannot open Pizza Hut, so she cannot ask about Sam.
        status, body = http_post(
            self.api("/copilot/messages/"),
            {
                "content": "Why?",
                "context": {"surface": "contacts", "view": "person", "contact": sam},
            },
            token=dana,
        )
        self.assertEqual(status, 400, body)
        self.assertEqual(body, {"context": {"contact": ["Not a person you can open."]}})
