"""End-to-end tier: real server, real HTTP, real test DB, the classifier
stubbed. A CSM adds people to an organisation and its account and logs two
calls: one the model reads, one with nothing to read. The person's sentiment
follows the call at once; the Contacts list filters to the organisation and
summarises it; the person's history shows both calls, one of them "not
analysable". A peer who cannot open the organisation gets a 404."""

from unittest.mock import patch
from urllib.parse import urlencode

from django.test import LiveServerTestCase

from e2e.http import http_get, http_post

WHEN = "2026-09-20T10:00:00Z"


def negative(batch, **_kwargs):
    return {
        f"{r._meta.model_name}:{r.pk}": {
            "ai_area": "customer_success",
            "ai_category": "onboarding",
            "ai_subcategory": "",
            "sentiment": "negative",
        }
        for r in batch
    }


class ContactsFlowTests(LiveServerTestCase):
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

    def create(self, path, payload, token):
        status, body = http_post(self.api(path), payload, token=token)
        self.assertEqual(status, 201, body)
        return body

    def read(self, path, token):
        status, body = http_get(self.api(path), token=token)
        self.assertEqual(status, 200, body)
        return body

    def test_full_flow(self):
        # 1. An organisation signs up; its admin adds two CSMs, who log in.
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

        # 2. Carl's organisation, its account, and one person on each.
        pizza = self.create("/customers/", {"name": "Pizza Hut"}, carl)["id"]
        emea = self.create(f"/customers/{pizza}/accounts/", {"name": "EMEA"}, carl)["id"]
        sam = self.create(
            f"/customers/{pizza}/contacts/",
            {"name": "Sam Pizza", "role": "decision_maker", "email": "sam@pizza.io"},
            carl,
        )
        uma = self.create(
            f"/customers/{pizza}/accounts/{emea}/contacts/",
            {"name": "Uma Hut", "role": "champion", "email": "uma@pizza.io"},
            carl,
        )

        # 3. A call the model reads, with Sam on it: Sam turns negative at once.
        with patch("services.customers.classification.classify_batch", side_effect=negative):
            call = self.create(
                f"/customers/{pizza}/calls/",
                {
                    "title": "Escalation",
                    "occurred_at": WHEN,
                    "summary": "They are unhappy with support.",
                    "participant_ids": [sam["id"]],
                },
                carl,
            )
        self.assertEqual((call["analysis"], call["sentiment"]), ("analysed", "negative"))
        # 4. A call with nothing to read is marked, never guessed.
        with patch("services.customers.classification.classify_batch") as model:
            empty = self.create(
                f"/customers/{pizza}/accounts/{emea}/calls/",
                {"title": "Weekly sync", "occurred_at": WHEN, "participant_ids": [sam["id"]]},
                carl,
            )
        model.assert_not_called()
        self.assertEqual(empty["analysis"], "not_analysable")

        # 5. The list filters to the organisation, names parents, and summarises.
        page = self.read("/contacts/?" + urlencode({"customer": pizza}), carl)
        rows = {r["name"]: r for r in page["results"]}
        self.assertEqual(set(rows), {"Sam Pizza", "Uma Hut"})
        self.assertEqual(rows["Uma Hut"]["account"], {"id": emea, "name": "EMEA"})
        self.assertEqual(rows["Sam Pizza"]["organisation"], {"id": pizza, "name": "Pizza Hut"})
        self.assertEqual(rows["Sam Pizza"]["sentiment"], "negative")
        # Both calls he was on are visible, whether or not each is evidence
        # (the not-analysable "Weekly sync" is visible but not evidence).
        self.assertEqual(rows["Sam Pizza"]["calls"], 2)
        self.assertEqual(
            page["summary"],
            {
                "total": 2,
                "positive": 0,
                "neutral": 1,
                "negative": 1,
                "decision_makers": 1,
                # Everyone here is new this month: no baseline to grow from.
                "active": 2,
                "growth_30d_pct": None,
            },
        )
        negative_only = self.read("/contacts/?" + urlencode({"sentiment": "negative"}), carl)
        self.assertEqual([r["name"] for r in negative_only["results"]], ["Sam Pizza"])

        # 6. Sam's history: both calls, newest first by time then id.
        history = self.read(f"/contacts/{sam['id']}/history/", carl)
        self.assertEqual(
            [(c["title"], c["analysis"], c["sentiment"]) for c in history["calls"]],
            [("Weekly sync", "not_analysable", None), ("Escalation", "analysed", "negative")],
        )
        self.assertEqual(history["calls"][0]["account"], {"id": emea, "name": "EMEA"})
        self.assertEqual(history["sentiment_readable"]["calls"], 1)

        # 7. Dana cannot open Carl's organisation, so not its people either.
        for person in (sam, uma):
            status, _ = http_get(self.api(f"/contacts/{person['id']}/history/"), token=dana)
            self.assertEqual(status, 404)
        self.assertEqual(self.read("/contacts/", dana)["summary"]["total"], 0)
