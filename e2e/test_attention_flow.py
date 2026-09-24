"""End-to-end tier: real server, real HTTP, real test DB. A CSM with a
renewal-due customer lists the Dashboard Overview's "Needs attention" list,
snoozes the item, no longer sees it, undoes the snooze, and sees it again."""

from datetime import timedelta

from django.test import LiveServerTestCase
from django.utils import timezone

from e2e.http import http_delete, http_get, http_post


class AttentionFlowTests(LiveServerTestCase):
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

        # 2. Admin adds a CSM to the organisation.
        status, body = http_post(
            self.api("/auth/users/"),
            {"name": "Carl CSM", "email": "carl@acme.io", "password": "csmpassword1"},
            token=admin_access,
        )
        self.assertEqual(status, 201, body)
        self.assertEqual(body["role"], "csm")

        # 3. The CSM logs in with their own credentials.
        status, body = http_post(
            self.api("/auth/login/"),
            {"email": "carl@acme.io", "password": "csmpassword1"},
        )
        self.assertEqual(status, 200, body)
        csm_access = body["access"]

        # 4. The CSM creates their own renewal-due customer (a new customer
        #    defaults to being owned by whoever creates it) with Poor health
        #    and ARR at stake, so it lands on their attention list as a
        #    "renewal" item.
        status, body = http_post(
            self.api("/customers/"),
            {
                "name": "Globex Corp",
                "health_score": "2.0",
                "arr_billed_at_account": "50000",
                "renewal_date": str(timezone.localdate() + timedelta(days=10)),
            },
            token=csm_access,
        )
        self.assertEqual(status, 201, body)
        customer_id = body["id"]
        key = f"renewal:{customer_id}"

        # 5. The CSM lists attention and sees the renewal item.
        status, body = http_get(self.api("/dashboard/attention/"), token=csm_access)
        self.assertEqual(status, 200, body)
        keys = {item["key"] for item in body["items"]}
        self.assertIn(key, keys)
        item = next(item for item in body["items"] if item["key"] == key)
        self.assertEqual(item["kind"], "renewal")
        self.assertEqual(item["customer_id"], customer_id)
        self.assertEqual(item["at_stake"], 50000.0)

        # 6. The CSM snoozes it for 7 days.
        status, body = http_post(
            self.api("/dashboard/attention/snooze/"),
            {"key": key, "days": 7},
            token=csm_access,
        )
        self.assertEqual(status, 201, body)
        self.assertEqual(body["key"], key)
        self.assertIsNotNone(body["until"])

        # 7. The CSM no longer sees it.
        status, body = http_get(self.api("/dashboard/attention/"), token=csm_access)
        self.assertEqual(status, 200, body)
        self.assertNotIn(key, {item["key"] for item in body["items"]})

        # 8. The CSM undoes the snooze.
        status, body = http_delete(
            self.api(f"/dashboard/attention/snooze/{key}/"), token=csm_access
        )
        self.assertEqual(status, 204, body)

        # 9. The CSM sees it again.
        status, body = http_get(self.api("/dashboard/attention/"), token=csm_access)
        self.assertEqual(status, 200, body)
        self.assertIn(key, {item["key"] for item in body["items"]})
