"""End-to-end tier: real server, real HTTP, real test DB. Proves the
customers flow: admin signs up -> admin adds a CSM -> admin creates a
customer -> CSM (not just admin) can see and edit it -> owner gets
assigned -> a second tenant can't see or touch the first tenant's
customers, and can't assign the first tenant's admin as an owner."""

from django.test import LiveServerTestCase

from e2e.http import http_get, http_patch, http_post


class CustomersFlowTests(LiveServerTestCase):
    def auth_api(self, path):
        return f"{self.live_server_url}/api/v1/auth{path}"

    def customers_api(self, path=""):
        return f"{self.live_server_url}/api/v1/customers/{path}"

    def test_full_flow(self):
        # 1. Organisation signs up.
        status, body = http_post(
            self.auth_api("/signup/"),
            {
                "organisation_name": "Acme Inc",
                "name": "Alice Admin",
                "email": "alice@acme.io",
                "password": "supersecret1",
            },
        )
        self.assertEqual(status, 201)
        admin_access = body["access"]

        # 2. Admin adds a CSM.
        status, body = http_post(
            self.auth_api("/csms/"),
            {"name": "Carl CSM", "email": "carl@acme.io", "password": "csmpassword1"},
            token=admin_access,
        )
        self.assertEqual(status, 201)
        csm_id = body["id"]

        status, body = http_post(
            self.auth_api("/login/"), {"email": "carl@acme.io", "password": "csmpassword1"}
        )
        self.assertEqual(status, 200)
        csm_access = body["access"]

        # 3. Admin creates a customer.
        status, body = http_post(
            self.customers_api(),
            {"name": "Globex Corp", "health_score": "8.2", "lifecycle_stage": "live"},
            token=admin_access,
        )
        self.assertEqual(status, 201)
        self.assertEqual(body["health_category"], "good")
        customer_id = body["id"]

        # 3b. The frontend's search box (name / Revenact ID) works over
        #     the real endpoint, not just in the ORM-level unit tests.
        status, body = http_get(self.customers_api("?search=globex"), token=admin_access)
        self.assertEqual(status, 200)
        self.assertEqual(body["count"], 1)

        status, body = http_get(self.customers_api(f"?search={customer_id}"), token=admin_access)
        self.assertEqual(status, 200)
        self.assertEqual(body["results"][0]["id"], customer_id)

        # 4. The CSM (not just the admin) can see it and edit it — customer
        #    records aren't admin-gated the way User Management is.
        status, body = http_get(self.customers_api(), token=csm_access)
        self.assertEqual(status, 200)
        self.assertEqual(body["count"], 1)

        status, body = http_patch(
            self.customers_api(f"{customer_id}/"), {"owner_id": csm_id}, token=csm_access
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["owner"]["email"], "carl@acme.io")

        # 5. A second organisation signs up independently.
        status, body = http_post(
            self.auth_api("/signup/"),
            {
                "organisation_name": "Other Org",
                "name": "Other Admin",
                "email": "other@other.io",
                "password": "othersecret1",
            },
        )
        self.assertEqual(status, 201)
        other_access = body["access"]
        other_admin_id = body["user"]["id"]

        # 6. It sees none of the first org's customers, and a direct
        #    lookup 404s (not 403 — can't distinguish "not yours" from
        #    "doesn't exist").
        status, body = http_get(self.customers_api(), token=other_access)
        self.assertEqual(status, 200)
        self.assertEqual(body["count"], 0)

        status, body = http_get(self.customers_api(f"{customer_id}/"), token=other_access)
        self.assertEqual(status, 404)

        # 7. The first org's admin can't assign the second org's admin as
        #    an owner — cross-tenant assignment is rejected.
        status, body = http_patch(
            self.customers_api(f"{customer_id}/"),
            {"owner_id": other_admin_id},
            token=admin_access,
        )
        self.assertEqual(status, 400)
