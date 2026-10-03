"""End-to-end tier: real server, real HTTP, real test DB.

Carl builds a segment of his low-CSAT organisations. He previews it, saves
it shared with the workspace with alerts on, and pins one more. Dana, a
shared viewer, sees only her own members and a count of the rest. She cannot
edit it, and duplicates it into her own. A night passes: Carl's history and
alert show who left. Carl exports what he sees and deletes the segment. A
segment Dana may not read answers exactly like one that does not exist."""

import csv
import io
from datetime import timedelta

from django.test import LiveServerTestCase
from django.utils import timezone

from e2e.http import http_delete, http_get, http_get_text, http_patch, http_post
from services.segments.nightly import evaluate_nightly

LOW_CSAT = {"match": "all", "conditions": [{"field": "csat_score", "op": "lt", "value": 60}]}


class SegmentsFlowTests(LiveServerTestCase):
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

    def names(self, body):
        return [row["name"] for row in body["results"]]

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

        # 2. Carl's two organisations and Dana's one, each owned by its creator.
        pizza = self.created("/customers/", {"name": "Pizza Hut", "csat_score": "40"}, carl)["id"]
        burger = self.created("/customers/", {"name": "Burger Barn", "csat_score": "85"}, carl)[
            "id"
        ]
        self.created("/customers/", {"name": "Taco Bell", "csat_score": "30"}, dana)

        # 3. The builder's preview: Carl's one low-CSAT organisation, not Dana's.
        status, body = http_post(
            self.api("/segments/preview/"), {"kind": "customer", "rules": LOW_CSAT}, token=carl
        )
        self.assertEqual(status, 200, body)
        self.assertEqual((body["count"], self.names(body)), (1, ["Pizza Hut"]))

        # 4. Saved, shared with the workspace, alerts on; then Burger Barn pinned in.
        segment = self.created(
            "/segments/",
            {
                "name": "Low CSAT",
                "kind": "customer",
                "rules": LOW_CSAT,
                "sharing": "workspace",
                "alert_on_changes": True,
            },
            carl,
        )
        sid = segment["id"]
        self.assertEqual(segment["member_count"], 1)
        status, body = http_patch(
            self.api(f"/segments/{sid}/members/{burger}/"), {"state": "pinned"}, token=carl
        )
        self.assertEqual((status, body["pinned_ids"]), (200, [burger]))
        status, body = http_get(self.api(f"/segments/{sid}/members/?sort=name"), token=carl)
        self.assertEqual(status, 200, body)
        self.assertEqual(self.names(body), ["Burger Barn", "Pizza Hut"])
        self.assertEqual((body["hidden_count"], body["summary"]["members"]), (0, 2))

        # 5. Dana: her own member and a count of Carl's; no edits; a copy of her own.
        status, body = http_get(self.api("/segments/?scope=shared"), token=dana)
        self.assertEqual([row["name"] for row in body], ["Low CSAT"])
        status, body = http_get(self.api(f"/segments/{sid}/members/"), token=dana)
        self.assertEqual((self.names(body), body["hidden_count"]), (["Taco Bell"], 2))
        status, body = http_get(self.api(f"/segments/{sid}/"), token=dana)
        self.assertEqual((body["pinned_ids"], body["is_owner"]), ([], False))
        status, body = http_patch(self.api(f"/segments/{sid}/"), {"name": "Mine now"}, token=dana)
        self.assertEqual(status, 403, body)
        copy = self.created(f"/segments/{sid}/duplicate/", {}, dana)
        self.assertEqual(
            (copy["name"], copy["sharing"], copy["pinned_ids"]), ("Low CSAT (copy)", "private", [])
        )
        status, body = http_get(self.api(f"/segments/{copy['id']}/members/"), token=dana)
        self.assertEqual((self.names(body), body["hidden_count"]), (["Taco Bell"], 0))

        # 6. A night passes: Pizza Hut's CSAT recovers and it leaves; Carl is told.
        status, body = http_patch(
            self.api(f"/customers/{pizza}/"), {"csat_score": "90"}, token=carl
        )
        self.assertEqual(status, 200, body)
        tomorrow = timezone.localdate() + timedelta(days=1)
        evaluate_nightly(today=tomorrow)
        status, body = http_get(self.api(f"/segments/{sid}/changes/"), token=carl)
        self.assertEqual(status, 200, body)
        self.assertEqual(
            body["days"],
            [
                {
                    "date": tomorrow.isoformat(),
                    "entered": [],
                    "left": [{"id": pizza, "name": "Pizza Hut", "reason": ["csat_score"]}],
                }
            ],
        )
        status, body = http_get(self.api("/notifications/"), token=carl)
        alerts = [row for row in body if row["kind"] == "segment_changes"]
        self.assertEqual(
            [(row["message"], row["link"]) for row in alerts],
            [("Low CSAT: 0 entered, 1 left", f"/segments/{sid}?tab=changes")],
        )

        # 7. Carl exports what he sees: the pinned organisation that stayed.
        status, content_type, text = http_get_text(
            self.api(f"/segments/{sid}/members/export.csv"), carl
        )
        self.assertEqual(status, 200)
        self.assertTrue(content_type.startswith("text/csv"))
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(
            (rows[0][0], [row[0] for row in rows[1:]]), ("Organization", ["Burger Barn"])
        )

        # 8. A segment Dana may not read answers like a missing one; Carl deletes his.
        private = self.created("/segments/", {"name": "Private", "kind": "customer"}, carl)["id"]
        hidden = http_get(self.api(f"/segments/{private}/"), token=dana)
        missing = http_get(self.api("/segments/999999/"), token=dana)
        self.assertEqual(hidden, missing)
        self.assertEqual(hidden[0], 404)
        status, _body = http_delete(self.api(f"/segments/{sid}/"), token=carl)
        self.assertEqual(status, 204)
        status, _body = http_get(self.api(f"/segments/{sid}/"), token=carl)
        self.assertEqual(status, 404)
