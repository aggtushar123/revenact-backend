"""End-to-end tier: real server, real HTTP, real test DB. An admin gathers
feature requests from classified asks, reads the ranked list with revenue,
opens one, marks it planned, and merges another into it."""

import json
from decimal import Decimal
from unittest.mock import patch

from django.test import LiveServerTestCase

from e2e.http import http_get, http_patch, http_post
from services.customers.models import Customer, Email
from services.customers.taxonomy import AICategory


class RequestsFlowTests(LiveServerTestCase):
    def api(self, path):
        return f"{self.live_server_url}/api/v1{path}"

    def test_full_flow(self):
        status, body = http_post(
            self.api("/auth/signup/"),
            {
                "organisation_name": "Acme Inc",
                "name": "Alice Admin",
                "email": "alice@acme.io",
                "password": "supersecret1",
            },
        )
        self.assertEqual(status, 201)
        admin = body["access"]
        status, body = http_post(
            self.api("/customers/"),
            {"name": "Globex Corp", "health_score": "8.2", "arr_billed_at_account": "50000"},
            token=admin,
        )
        self.assertEqual(status, 201)
        globex = Customer.objects.get(id=body["id"])
        for subject, body_text in [
            ("Slack alerts", "Alerts in Slack?"),
            ("Dark mode", "A dark theme please"),
        ]:
            Email.objects.create(
                customer=globex,
                subject=subject,
                body=body_text,
                sent_at="2026-09-01T09:00:00Z",
                ai_category=AICategory.FEATURE_REQUEST,
                ai_classified_at="2026-09-01T09:00:00Z",
            )
        vectors = {
            "Slack alerts. Alerts in Slack?": [1.0, 0.0],
            "Dark mode. A dark theme please": [0.0, 1.0],
        }
        titles = [
            json.dumps({"title": "Slack alerts", "summary": "s"}),
            json.dumps({"title": "Dark mode", "summary": "d"}),
        ]
        with (
            patch(
                "services.requests.gather.embed",
                side_effect=lambda texts: [vectors[t] for t in texts],
            ),
            patch("services.requests.gather.get_completion", side_effect=titles),
        ):
            status, body = http_post(self.api("/requests/gather/"), {}, token=admin)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["created"], 2)

        status, rows = http_get(self.api("/requests/"), token=admin)
        self.assertEqual(status, 200)
        self.assertEqual(len(rows), 2)
        self.assertEqual(Decimal(rows[0]["arr"]), Decimal("50000"))
        slack = next(r for r in rows if r["title"] == "Slack alerts")
        dark = next(r for r in rows if r["title"] == "Dark mode")

        status, body = http_patch(
            self.api(f"/requests/{slack['id']}/"), {"status": "planned"}, token=admin
        )
        self.assertEqual(status, 200, body)
        status, body = http_post(
            self.api(f"/requests/{dark['id']}/merge/"), {"into": slack["id"]}, token=admin
        )
        self.assertEqual(status, 200, body)
        status, detail = http_get(self.api(f"/requests/{slack['id']}/"), token=admin)
        self.assertEqual(status, 200)
        self.assertEqual(detail["status"], "planned")
        self.assertEqual(len(detail["evidence"]), 2)
