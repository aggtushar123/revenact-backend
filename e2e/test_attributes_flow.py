"""End-to-end tier: real server, real HTTP, real test DB. An admin defines
an AI attribute, a CSM fills it on a customer, reads the value with its
reasoning, overrides it, and the history shows both rows."""

import json
from unittest.mock import patch

from django.test import LiveServerTestCase

from e2e.http import http_get, http_post


class AttributesFlowTests(LiveServerTestCase):
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
            self.api("/customers/"), {"name": "Globex Corp", "health_score": "8.2"}, token=admin
        )
        self.assertEqual(status, 201)
        customer_id = body["id"]

        status, body = http_post(
            self.api("/attributes/definitions/"),
            {
                "name": "Primary use case",
                "prompt": "What does this company mainly use our product for?",
                "value_type": "text",
                "applies_to_customer": True,
            },
            token=admin,
        )
        self.assertEqual(status, 201, body)
        attribute_id = body["id"]

        reply = json.dumps(
            {"value": "Field service scheduling", "reasoning": "No notes yet.", "evidence": []}
        )
        with patch("services.attributes.fill.get_completion", return_value=reply):
            status, body = http_post(
                self.api(f"/attributes/definitions/{attribute_id}/fill/"),
                {"customer": customer_id},
                token=admin,
            )
        self.assertEqual(status, 201, body)
        self.assertEqual(body["value"], "Field service scheduling")

        status, body = http_get(
            self.api(f"/attributes/values/?customer={customer_id}"), token=admin
        )
        self.assertEqual(status, 200)
        self.assertEqual(body[0]["latest"]["reasoning"], "No notes yet.")

        status, body = http_post(
            self.api("/attributes/values/"),
            {"attribute": attribute_id, "customer": customer_id, "value": "Dispatch"},
            token=admin,
        )
        self.assertEqual(status, 201, body)

        status, body = http_get(
            self.api(
                f"/attributes/values/history/?attribute={attribute_id}&customer={customer_id}"
            ),
            token=admin,
        )
        self.assertEqual(status, 200)
        self.assertEqual([row["origin"] for row in body], ["human", "ai"])
