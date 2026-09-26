"""End-to-end tier: real server, real HTTP, real test DB. A CSM asks Ask
Revenact on Organizations with the list filtered to their own Poor accounts,
mentioning a colleague who has no visibility into that book; opens a row
(naming the admin's account too, which is dropped); follows up from the
Board; is refused an attention focus; finds the conversation in history with
the labels and filters that restore the page; and the mentioned colleague, a
slice reader in the shared conversation, is withheld the reply because it is
grounded on a customer she cannot see. The model call is stubbed in-process.

Adapted from the task brief: the brief's flow otherwise matches the built
grounding and context shapes exactly, but it predates Task 5's shared-session
redesign (a reply's book is no longer rebuilt from the asker's *current*
filters when it is read; it is a snapshot of customer ids fixed on the reply
when it was written — `Message.grounded_customer_ids`/`carries_anomaly_text`,
migration 0013). Step 4b below is added, per the controller's instruction, to
exercise that snapshot end to end rather than only at the unit/integration
tier (`services/copilot/tests/test_organizations_readability.py`)."""

from datetime import timedelta
from unittest.mock import patch

from django.test import LiveServerTestCase
from django.utils import timezone

from e2e.http import http_get, http_post


def organizations(view="list", focus=None, **filters):
    return {"surface": "organizations", "view": view, "filters": filters, "focus": focus}


class OrganizationsAskFlowTests(LiveServerTestCase):
    def api(self, path):
        return f"{self.live_server_url}/api/v1{path}"

    def test_full_flow(self):
        # 1. An organisation signs up; its admin adds a CSM, a colleague with
        # no book of her own, and an account of the admin's own.
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
        status, body = http_post(
            self.api("/auth/users/"),
            {"name": "Carl CSM", "email": "carl@acme.io", "password": "csmpassword1"},
            token=admin,
        )
        self.assertEqual(status, 201, body)
        status, body = http_post(
            self.api("/auth/users/"),
            {"name": "Priya Nair", "email": "priya@acme.io", "password": "priyapassword1"},
            token=admin,
        )
        self.assertEqual(status, 201, body)
        status, body = http_post(
            self.api("/customers/"),
            {"name": "Initech", "health_score": "2.0", "arr_billed_at_account": "70000"},
            token=admin,
        )
        self.assertEqual(status, 201, body)
        foreign_id = body["id"]

        # 2. The CSM and the colleague log in. The colleague is a plain CSM
        # (the default role, no capabilities) and owns nothing: she is not
        # Carl's manager and has no view of his book.
        status, body = http_post(
            self.api("/auth/login/"), {"email": "carl@acme.io", "password": "csmpassword1"}
        )
        self.assertEqual(status, 200, body)
        csm = body["access"]
        status, body = http_post(
            self.api("/auth/login/"), {"email": "priya@acme.io", "password": "priyapassword1"}
        )
        self.assertEqual(status, 200, body)
        priya = body["access"]

        # 3. The CSM adds a Poor account renewing in 10 days and a Good one.
        ids = {}
        soon = str(timezone.localdate() + timedelta(days=10))
        for name, score, arr, extra in (
            ("Globex Corp", "2.0", "50000", {"renewal_date": soon}),
            ("Hooli", "8.0", "20000", {}),
        ):
            status, body = http_post(
                self.api("/customers/"),
                {
                    "name": name,
                    "health_score": score,
                    "arr_billed_at_account": arr,
                    "lifecycle_stage": "live",
                    **extra,
                },
                token=csm,
            )
            self.assertEqual(status, 201, body)
            ids[name] = body["id"]

        # 4. The page's own owner option for Carl, and the list the rail will ask about.
        status, body = http_get(self.api("/organizations/portfolio/"), token=csm)
        self.assertEqual(status, 200, body)
        carl = next(o["value"] for o in body["filters"]["owners"] if o["name"] == "Carl CSM")
        status, listed = http_get(
            self.api(f"/organizations/portfolio/?owner={carl}&health=poor"), token=csm
        )
        self.assertEqual((status, listed["summary"]["accounts"]), (200, 1))

        with patch(
            "services.copilot.views.get_completion", return_value="Globex Corp renews first."
        ) as completion:
            # 5. Ask with the list filtered, mentioning Priya: the digest is
            # that list, and nothing else, and the mention routes a question
            # to her.
            status, body = http_post(
                self.api("/copilot/messages/"),
                {
                    "content": "@Priya Nair, which accounts need me first?",
                    "context": organizations(owner=carl, health="poor"),
                },
                token=csm,
            )
            self.assertEqual(status, 200, body)
            conversation_id = body["id"]
            system = completion.call_args.kwargs["system"]
            self.assertEqual(completion.call_args.kwargs["purpose"], "organizations")
            self.assertIn("Screen: Organizations › List", system)
            self.assertIn("Filters: Owner: Carl CSM; Health: Poor", system)
            self.assertIn(f"Accounts in view: {listed['summary']['accounts']};", system)
            self.assertIn("Globex Corp", system)
            self.assertNotIn("Hooli", system)
            self.assertNotIn("Initech", system)
            origin = {
                "surface": "organizations",
                "view": "list",
                "filters": {"owner": carl, "health": "poor"},
                "labels": ["Owner: Carl CSM", "Health: Poor"],
            }
            self.assertEqual(body["origin"], origin)
            self.assertEqual(body["messages"][0]["context"], {**origin, "focus": None})

            # 5b. Shared session: Priya was mentioned in the ask itself, so
            # she reads the conversation as a slice — her own turn (the
            # question that named her), but not the reply, because it was
            # written from Carl's book (Globex Corp) and Globex Corp is
            # outside anything Priya owns or manages. Written per Task 5's
            # snapshot rule (`grounded_customer_ids` fixed on the reply when
            # it was sent), not a book rebuilt at read time.
            status, body = http_get(
                self.api(f"/copilot/conversations/{conversation_id}/"), token=priya
            )
            self.assertEqual(status, 200, body)
            self.assertEqual(body["visibility"], "partial")
            self.assertIn("Priya Nair", body["messages"][0]["content"])
            self.assertNotIn("Globex Corp renews first.", body["messages"][1]["content"])
            self.assertIn("isn't shared with you", body["messages"][1]["content"])

            # 6. Open a row: the admin's id in the focus is dropped silently.
            status, body = http_post(
                self.api("/copilot/messages/"),
                {
                    "conversation_id": conversation_id,
                    "content": "Will Globex Corp renew?",
                    "context": organizations(
                        focus={"kind": "companies", "ids": [ids["Globex Corp"], foreign_id]},
                        owner=carl,
                        health="poor",
                    ),
                },
                token=csm,
            )
            self.assertEqual(status, 200, body)
            self.assertEqual(
                body["messages"][2]["context"]["focus"],
                {"kind": "companies", "ids": [ids["Globex Corp"]]},
            )
            self.assertNotIn("Initech", completion.call_args.kwargs["system"])

            # 7. From the Board: the new screen, the old origin.
            status, body = http_post(
                self.api("/copilot/messages/"),
                {
                    "conversation_id": conversation_id,
                    "content": "And the whole board?",
                    "context": organizations("board"),
                },
                token=csm,
            )
            self.assertEqual(status, 200, body)
            system = completion.call_args.kwargs["system"]
            self.assertIn("Screen: Organizations › Board", system)
            self.assertIn("Sections, grouped by lifecycle stage:", system)
            # Adaptation: the built digest never names an individual company in
            # its sections (only counts and ARR per bucket) — it names
            # companies only in the riskiest/renewals lists and the focused
            # records. Hooli has a Good score and no renewal date, so it is in
            # neither list; unlike the filtered List above, its ARR is now
            # counted, proving the filter really was dropped.
            self.assertIn("Accounts in view: 2; ARR 70,000.00 USD", system)
            self.assertEqual(body["origin"], origin)

            # 8. An attention focus belongs to the Dashboard only.
            status, body = http_post(
                self.api("/copilot/messages/"),
                {
                    "content": "Why?",
                    "context": organizations(
                        focus={"kind": "attention", "key": f"renewal:{ids['Globex Corp']}"}
                    ),
                },
                token=csm,
            )
            self.assertEqual(status, 400, body)
            self.assertEqual(body, {"context": {"focus": {"kind": ["Must be companies."]}}})

        # 9. History: the tag's labels, and the filters that restore the page.
        status, body = http_get(self.api("/copilot/conversations/"), token=csm)
        self.assertEqual(status, 200, body)
        self.assertEqual({row["id"]: row for row in body}[conversation_id]["origin"], origin)
        status, body = http_get(self.api(f"/copilot/conversations/{conversation_id}/"), token=csm)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["origin"]["filters"], {"owner": carl, "health": "poor"})
