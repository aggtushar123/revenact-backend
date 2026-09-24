"""End-to-end tier: real server, real HTTP, real test DB. A CSM asks on the
Dashboard Overview, follows up after changing the filter, asks about a drill's
companies (one of them someone else's), presses "Why?" on an attention row, is
refused a key that is not theirs, and finds the conversation in history
tagged with where it started. The model call is stubbed in-process."""

from datetime import timedelta
from unittest.mock import patch

from django.test import LiveServerTestCase
from django.utils import timezone

from e2e.http import http_get, http_post

FILTERS = {"owner": "", "lifecycle": "", "customer": ""}


def dashboard(area="overview", view=None, focus=None, **filters):
    return {
        "surface": "dashboard",
        "area": area,
        "view": view,
        "filters": {**FILTERS, **filters},
        "focus": focus,
    }


class DashboardAskFlowTests(LiveServerTestCase):
    def api(self, path):
        return f"{self.live_server_url}/api/v1{path}"

    def test_full_flow(self):
        # 1. Organisation signs up (creates org + admin, logs the admin in).
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
        admin_access = body["access"]

        # 2. Admin adds a CSM, and creates a customer of their own (not the CSM's).
        status, body = http_post(
            self.api("/auth/users/"),
            {"name": "Carl CSM", "email": "carl@acme.io", "password": "csmpassword1"},
            token=admin_access,
        )
        self.assertEqual(status, 201, body)
        status, body = http_post(
            self.api("/customers/"),
            {"name": "Initech", "health_score": "2.0", "arr_billed_at_account": "70000"},
            token=admin_access,
        )
        self.assertEqual(status, 201, body)
        foreign_id = body["id"]

        # 3. The CSM logs in and creates a renewal-due, Poor customer.
        status, body = http_post(
            self.api("/auth/login/"), {"email": "carl@acme.io", "password": "csmpassword1"}
        )
        self.assertEqual(status, 200, body)
        csm = body["access"]
        status, body = http_post(
            self.api("/customers/"),
            {
                "name": "Globex Corp",
                "health_score": "2.0",
                "arr_billed_at_account": "50000",
                "renewal_date": str(timezone.localdate() + timedelta(days=10)),
            },
            token=csm,
        )
        self.assertEqual(status, 201, body)
        customer_id = body["id"]

        with patch(
            "services.copilot.views.get_completion", return_value="Globex renews soon."
        ) as completion:
            # 4. Ask on the Overview.
            status, body = http_post(
                self.api("/copilot/messages/"),
                {"content": "What needs me first?", "context": dashboard()},
                token=csm,
            )
            self.assertEqual(status, 200, body)
            conversation_id = body["id"]
            self.assertEqual(body["origin"], {k: v for k, v in dashboard().items() if k != "focus"})
            self.assertEqual(body["messages"][0]["context"], dashboard())
            self.assertEqual(completion.call_args.kwargs["purpose"], "dashboard")
            self.assertIn("Screen: Overview", completion.call_args.kwargs["system"])
            self.assertIn("Globex Corp", completion.call_args.kwargs["system"])
            self.assertNotIn("Initech", completion.call_args.kwargs["system"])

            # 5. Change a filter and ask a follow-up: the new screen, the old origin.
            follow_up = dashboard("revenue", "forecast", customer=str(customer_id))
            status, body = http_post(
                self.api("/copilot/messages/"),
                {
                    "conversation_id": conversation_id,
                    "content": "And revenue?",
                    "context": follow_up,
                },
                token=csm,
            )
            self.assertEqual(status, 200, body)
            self.assertEqual(body["messages"][2]["context"], follow_up)
            self.assertEqual(body["origin"]["area"], "overview")
            self.assertIn("Screen: Revenue › Forecast", completion.call_args.kwargs["system"])

            # 6. "Ask about these" from a drill: someone else's id is dropped silently.
            status, body = http_post(
                self.api("/copilot/messages/"),
                {
                    "conversation_id": conversation_id,
                    "content": "Why are these in At risk?",
                    "context": dashboard(
                        focus={"kind": "companies", "ids": [customer_id, foreign_id]}
                    ),
                },
                token=csm,
            )
            self.assertEqual(status, 200, body)
            self.assertEqual(
                body["messages"][4]["context"]["focus"],
                {"kind": "companies", "ids": [customer_id]},
            )
            self.assertNotIn("Initech", completion.call_args.kwargs["system"])

            # 7. "Why?" on an attention row.
            status, body = http_get(self.api("/dashboard/attention/"), token=csm)
            self.assertEqual(status, 200, body)
            key = f"renewal:{customer_id}"
            self.assertIn(key, {item["key"] for item in body["items"]})
            status, body = http_post(
                self.api("/copilot/messages/"),
                {
                    "content": "Why is this on my list?",
                    "context": dashboard(focus={"kind": "attention", "key": key}),
                },
                token=csm,
            )
            self.assertEqual(status, 200, body)

            # 8. A key that is not on the CSM's list is refused, generically.
            status, body = http_post(
                self.api("/copilot/messages/"),
                {
                    "content": "Why?",
                    "context": dashboard(
                        focus={"kind": "attention", "key": f"renewal:{foreign_id}"}
                    ),
                },
                token=csm,
            )
            self.assertEqual(status, 400, body)
            self.assertEqual(body, {"context": {"focus": {"key": ["Not an item on your list."]}}})

        # 9. History shows where the first conversation started.
        status, body = http_get(self.api("/copilot/conversations/"), token=csm)
        self.assertEqual(status, 200, body)
        by_id = {row["id"]: row for row in body}
        self.assertEqual(by_id[conversation_id]["origin"]["area"], "overview")
