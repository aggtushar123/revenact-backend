"""End-to-end tier: a real server thread + real test DB, hit over actual
HTTP (stdlib urllib, not self.client's in-process shortcut — no new
dependency) — proves the full login flow described in the auth-flow doc:
org signs up -> admin logs in -> admin adds a CSM -> CSM logs in with
their own credentials -> CSM edits their own profile -> admin manages
the CSM via User Management (list, reset password, deactivate) -> admin
logs out and their own refresh token stops working."""

from django.test import LiveServerTestCase

from e2e.http import http_get, http_patch, http_post


class OrgSignupThenCSMLoginFlowTests(LiveServerTestCase):
    def api(self, path):
        return f"{self.live_server_url}/api/v1/auth{path}"

    def test_full_flow(self):
        # 1. Organisation signs up (creates org + admin, logs the admin in).
        status, body = http_post(
            self.api("/signup/"),
            {
                "organisation_name": "Acme Inc",
                "name": "Alice Admin",
                "email": "alice@acme.io",
                "password": "supersecret1",
            },
        )
        self.assertEqual(status, 201)

        # 2. Admin logs in again independently (separate from signup).
        status, body = http_post(
            self.api("/login/"),
            {"email": "alice@acme.io", "password": "supersecret1"},
        )
        self.assertEqual(status, 200)
        admin_access = body["access"]
        admin_refresh = body["refresh"]

        # 3. Admin adds a CSM to their organisation.
        status, body = http_post(
            self.api("/csms/"),
            {"name": "Carl CSM", "email": "carl@acme.io", "password": "csmpassword1"},
            token=admin_access,
        )
        self.assertEqual(status, 201)
        self.assertEqual(body["role"], "csm")
        org_id = body["organisation"]["id"]

        # 4. The CSM logs in with the credentials the admin set, and lands
        #    in the same organisation.
        status, body = http_post(
            self.api("/login/"),
            {"email": "carl@acme.io", "password": "csmpassword1"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["user"]["organisation"]["id"], org_id)
        self.assertEqual(body["user"]["role"], "csm")
        csm_access = body["access"]
        csm_refresh = body["refresh"]

        # 5. The CSM cannot add other CSMs — only the org admin can.
        status, body = http_post(
            self.api("/csms/"),
            {"name": "Someone Else", "email": "someone@acme.io", "password": "whatever12"},
            token=csm_access,
        )
        self.assertEqual(status, 403)

        # 6. The CSM views and edits their own profile.
        status, body = http_get(self.api("/me/"), token=csm_access)
        self.assertEqual(status, 200)
        self.assertEqual(body["email"], "carl@acme.io")

        status, body = http_patch(self.api("/me/"), {"name": "Carl Renamed"}, token=csm_access)
        self.assertEqual(status, 200)
        self.assertEqual(body["name"], "Carl Renamed")

        # ...but not someone else's — User Management is admin-only.
        status, body = http_get(self.api("/csms/"), token=csm_access)
        self.assertEqual(status, 403)

        # 7. The admin manages the CSM via User Management: list, then edit.
        status, body = http_get(self.api("/csms/"), token=admin_access)
        self.assertEqual(status, 200)
        self.assertEqual([row["email"] for row in body["results"]], ["carl@acme.io"])
        csm_id = body["results"][0]["id"]

        status, body = http_patch(
            self.api(f"/csms/{csm_id}/"), {"password": "newcsmpassword1"}, token=admin_access
        )
        self.assertEqual(status, 200)

        status, body = http_post(
            self.api("/login/"), {"email": "carl@acme.io", "password": "newcsmpassword1"}
        )
        self.assertEqual(status, 200)

        # 8. The admin deactivates the CSM — their still-valid access token
        #    is rejected on its very next request, not just future logins.
        status, body = http_patch(
            self.api(f"/csms/{csm_id}/"), {"is_active": False}, token=admin_access
        )
        self.assertEqual(status, 200)

        status, body = http_get(self.api("/me/"), token=csm_access)
        self.assertEqual(status, 401)

        status, body = http_post(
            self.api("/login/"), {"email": "carl@acme.io", "password": "newcsmpassword1"}
        )
        self.assertEqual(status, 401)

        # Deactivating also blacklisted the CSM's refresh token from step 4
        # (not just their access token above).
        status, body = http_post(self.api("/token/refresh/"), {"refresh": csm_refresh})
        self.assertEqual(status, 401)

        # 9. The admin logs out — their own refresh token is blacklisted and
        #    can no longer be used to mint a new access token.
        status, body = http_post(
            self.api("/logout/"), {"refresh": admin_refresh}, token=admin_access
        )
        self.assertEqual(status, 205)

        status, body = http_post(self.api("/token/refresh/"), {"refresh": admin_refresh})
        self.assertEqual(status, 401)
