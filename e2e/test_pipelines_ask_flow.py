"""End-to-end tier: real server, real HTTP, real test DB.

A CSM asks Ask Revenact from the Pipelines List about his opportunities.
Then, in the same conversation, he asks from the risks Board about one risk,
mentioning a colleague. The History tags the conversation with where it
started. The colleague, mentioned, reads the questions but neither reply:
each counted an organisation she cannot open. She cannot ask about that
organisation or its deal either, and a filter or item that does not exist
is refused exactly like one she may not open. The model call is stubbed
in-process."""

from datetime import timedelta
from unittest.mock import patch

from django.test import LiveServerTestCase
from django.utils import timezone

from e2e.http import http_get, http_post

NOT_OPEN_ORGANISATION = {
    "context": {"filters": {"organisation": ["Not an organisation you can open."]}}
}
REDACTED = "This reply isn't shared with you: it draws on records outside what you may see."
OPEN_DEAL_STAGES = (
    "Discovery, Qualification, Solution Validation, Proposal / Price Review, Negotiation"
)
NOT_OPEN_ITEM = {"context": {"focus": ["Not an opportunity or risk you can open."]}}


class PipelinesAskFlowTests(LiveServerTestCase):
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

    def ask(self, context, token, content="What is going on?", conversation=None):
        payload = {"content": content, "context": context}
        if conversation is not None:
            payload["conversation_id"] = conversation
        return http_post(self.api("/copilot/messages/"), payload, token=token)

    def test_full_flow(self):
        today = timezone.localdate()

        def day(n):
            return (today + timedelta(days=n)).isoformat()

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

        # 2. Carl's organisation and account, a deal on each, an open risk on
        #    the organisation and a mitigated one on the account.
        pizza = self.created("/customers/", {"name": "Pizza Hut"}, carl)["id"]
        emea = self.created(f"/customers/{pizza}/accounts/", {"name": "Pizza EMEA"}, carl)["id"]
        upsell = self.created(
            "/opportunities/",
            {"customer_id": pizza, "title": "Upsell", "mrr": "3000", "expected_close": day(-5)},
            carl,
        )["id"]
        self.created(
            "/opportunities/",
            {"account_id": emea, "title": "EMEA seats", "mrr": "2000", "expected_close": day(7)},
            carl,
        )
        risk = self.created(
            "/risks/",
            {"customer_id": pizza, "title": "Budget cut", "mrr": "500", "due_by": day(20)},
            carl,
        )["id"]
        self.created(
            "/risks/",
            {"account_id": emea, "title": "Old churn", "mrr": "100", "stage": "mitigated"},
            carl,
        )

        with patch(
            "services.copilot.views.get_completion", return_value="Chase Upsell first."
        ) as completion:
            # 3. Carl asks from the List of opportunities.
            status, body = self.ask(
                {"surface": "pipelines", "kind": "opportunities", "view": "list", "filters": {}},
                carl,
                "What should I chase?",
            )
            self.assertEqual(status, 200, body)
            conversation = body["id"]
            system = completion.call_args.kwargs["system"]
            self.assertEqual(completion.call_args.kwargs["purpose"], "pipelines")
            self.assertIn("Pipelines data:\n<dashboard_data>", system)
            self.assertIn("Screen: Pipelines › Opportunities › List", system)
            self.assertIn("Stages listed: " + OPEN_DEAL_STAGES, system)
            self.assertIn("Largest open opportunities listed, by MRR (2):", system)
            self.assertNotIn("Largest closed", system)
            self.assertIn("Overdue, most overdue first (1):", system)
            self.assertIn("Closing within 90 days, soonest first (1):", system)
            self.assertIn("  - Upsell — Pizza Hut (organisation): MRR 3,000.00 USD", system)
            self.assertIn("  - EMEA seats — Pizza EMEA (account): MRR 2,000.00 USD", system)
            list_origin = {
                "surface": "pipelines",
                "kind": "opportunities",
                "view": "list",
                "filters": {},
                "label": "Pipelines · Opportunities",
            }
            self.assertEqual(body["origin"], list_origin)

            # 4. In the same conversation, from the risks Board, "Ask about
            #    this" on the risk, mentioning Dana.
            status, body = self.ask(
                {
                    "surface": "pipelines",
                    "kind": "risks",
                    "view": "board",
                    "filters": {},
                    "focus": {"kind": "risk", "id": risk},
                },
                carl,
                "@Dana CSM, should we worry about this?",
                conversation,
            )
            self.assertEqual(status, 200, body)
            system = completion.call_args.kwargs["system"]
            self.assertIn("Screen: Pipelines › Risks › Board", system)
            # The Board lists every stage when no stage filter is set
            # (owner ruling 2026-10-01), so the closed risks are quoted in a
            # list of their own beside the open ones.
            self.assertIn("Stages listed: Open, Mitigated, Realised, Abandoned", system)
            self.assertIn("Largest open risks listed, by MRR (1):", system)
            self.assertIn("Largest closed risks listed, by MRR (1):", system)
            self.assertIn("  - Old churn — Pizza EMEA (account): MRR 100.00 USD, Mitigated", system)
            self.assertIn("Due within 90 days, soonest first (1):", system)
            self.assertIn("The asker is asking about this risk:", system)
            self.assertIn("  - Budget cut — Pizza Hut (organisation)", system)
            self.assertEqual(body["origin"], list_origin)
            asked = [m["context"] for m in body["messages"] if m["role"] == "user"]
            self.assertEqual(
                asked[-1],
                {
                    "surface": "pipelines",
                    "kind": "risks",
                    "view": "board",
                    "filters": {},
                    "label": "Pipelines · Risks",
                    "focus": {"kind": "risk", "id": risk},
                },
            )

            # 5. The History tags the conversation with where it started.
            status, listed = http_get(self.api("/copilot/conversations/"), token=carl)
            self.assertEqual(status, 200, listed)
            self.assertEqual([c["origin"] for c in listed], [list_origin])

            # 6. Dana, mentioned, reads the questions but neither reply: each
            #    counted Pizza Hut, which she cannot open. She sees Carl's
            #    first question too, not only the one that mentions her:
            #    Carl is in her scope (same function, `hierarchy.scope_ids`).
            status, body = http_get(self.api(f"/copilot/conversations/{conversation}/"), token=dana)
            self.assertEqual(status, 200, body)
            self.assertEqual(body["visibility"], "partial")
            self.assertEqual(
                [(m["role"], m["content"]) for m in body["messages"]],
                [
                    ("user", "What should I chase?"),
                    ("assistant", REDACTED),
                    ("user", "@Dana CSM, should we worry about this?"),
                    ("assistant", REDACTED),
                ],
            )

            # 7. Dana cannot ask about Pizza Hut or its deal. A filter or an
            #    item that does not exist reads exactly the same.
            calls = completion.call_count
            for organisation in (str(pizza), "999999"):
                status, body = self.ask(
                    {
                        "surface": "pipelines",
                        "kind": "opportunities",
                        "view": "list",
                        "filters": {"organisation": organisation},
                    },
                    dana,
                )
                self.assertEqual((status, body), (400, NOT_OPEN_ORGANISATION))
            for token, ident in ((dana, upsell), (carl, 999999)):
                status, body = self.ask(
                    {
                        "surface": "pipelines",
                        "kind": "opportunities",
                        "view": "board",
                        "filters": {},
                        "focus": {"kind": "opportunity", "id": ident},
                    },
                    token,
                )
                self.assertEqual((status, body), (400, NOT_OPEN_ITEM))
            self.assertEqual(completion.call_count, calls)
