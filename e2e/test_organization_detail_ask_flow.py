"""End-to-end tier: real server, real HTTP, real test DB. A CSM asks Ask
Revenact on his organisation's page, narrowed to an account and about one
story item, mentioning the admin; the history tags the conversation with the
page. The admin, mentioned, sees everything but not the CSM's own note, so the
reply that quoted it is withheld from her — though she still sees where it
was asked. A peer cannot ask about the page at all. On a page with nothing
private, the admin, mentioned, reads the reply whole. The model call is
stubbed in-process."""

from unittest.mock import patch

from django.test import LiveServerTestCase

from e2e.http import http_get, http_post


def detail(organization, account=None, focus=None):
    return {
        "surface": "organizations",
        "view": "detail",
        "organization": organization,
        "account": account,
        "focus": focus,
    }


class OrganizationDetailAskFlowTests(LiveServerTestCase):
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

    def test_full_flow(self):
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

        # 2. Carl's organisation, one account, and his own note on the account.
        status, body = http_post(self.api("/customers/"), {"name": "Pizza Hut"}, token=carl)
        self.assertEqual(status, 201, body)
        pizza = body["id"]
        status, body = http_post(
            self.api(f"/customers/{pizza}/accounts/"), {"name": "EMEA"}, token=carl
        )
        self.assertEqual(status, 201, body)
        emea = body["id"]
        status, body = http_post(
            self.api(f"/customers/{pizza}/accounts/{emea}/notes/"),
            {"title": "Champion left", "body": "Sam moved on to Globex."},
            token=carl,
        )
        self.assertEqual(status, 201, body)
        note = body["id"]

        with patch(
            "services.copilot.views.get_completion", return_value="Sam's exit is the risk."
        ) as completion:
            # 3. Carl asks about the note on the EMEA chip, mentioning Alice.
            status, body = http_post(
                self.api("/copilot/messages/"),
                {
                    "content": "@Alice Admin, what does this mean for EMEA?",
                    "context": detail(pizza, emea, {"kind": "note", "id": note}),
                },
                token=carl,
            )
            self.assertEqual(status, 200, body)
            conversation = body["id"]
            system = completion.call_args.kwargs["system"]
            self.assertEqual(completion.call_args.kwargs["purpose"], "organizations")
            self.assertIn("Screen: Organizations › Pizza Hut · EMEA", system)
            self.assertIn("The asker is asking about this story item:", system)
            self.assertIn("Champion left", system)
            origin = {
                "surface": "organizations",
                "view": "detail",
                "organization": pizza,
                "account": emea,
                "label": "Pizza Hut · EMEA",
            }
            self.assertEqual(body["origin"], origin)

            # 4. The history tags the conversation with the page.
            status, body = http_get(self.api("/copilot/conversations/"), token=carl)
            self.assertEqual(status, 200, body)
            self.assertEqual(body[0]["origin"], origin)

            # 5. Alice, mentioned, reads her question but not the reply: it
            #    quoted Carl's own note, which only he and his chain may read.
            status, body = http_get(
                self.api(f"/copilot/conversations/{conversation}/"), token=admin
            )
            self.assertEqual(status, 200, body)
            self.assertEqual(body["visibility"], "partial")
            self.assertIn("Alice Admin", body["messages"][0]["content"])
            self.assertIn("isn't shared with you", body["messages"][1]["content"])
            # Deliberately, she still sees where it was asked: company names
            # are visible across the organisation, so the user turn keeps
            # its label and the conversation its origin.
            self.assertEqual(body["messages"][0]["context"]["label"], "Pizza Hut · EMEA")
            self.assertEqual(body["origin"], origin)

            # 6. Dana cannot ask about Carl's organisation at all.
            calls = completion.call_count
            status, body = http_post(
                self.api("/copilot/messages/"),
                {"content": "What is going on?", "context": detail(pizza)},
                token=dana,
            )
            self.assertEqual(status, 400, body)
            self.assertEqual(
                body, {"context": {"organization": ["Not an organisation you can open."]}}
            )
            self.assertEqual(completion.call_count, calls)

            # 7. On a page with nothing only Carl may read, Alice, mentioned,
            #    reads the reply whole.
            status, body = http_post(self.api("/customers/"), {"name": "Taco Co"}, token=carl)
            self.assertEqual(status, 201, body)
            taco = body["id"]
            status, body = http_post(
                self.api(f"/customers/{taco}/accounts/"), {"name": "LATAM"}, token=carl
            )
            self.assertEqual(status, 201, body)
            status, body = http_post(
                self.api("/copilot/messages/"),
                {"content": "@Alice Admin, how is Taco Co doing?", "context": detail(taco)},
                token=carl,
            )
            self.assertEqual(status, 200, body)
            shared = body["id"]
            status, body = http_get(self.api(f"/copilot/conversations/{shared}/"), token=admin)
            self.assertEqual(status, 200, body)
            self.assertEqual(body["messages"][1]["content"], "Sam's exit is the risk.")
            self.assertEqual(body["messages"][0]["context"]["label"], "Taco Co")
