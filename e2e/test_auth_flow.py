"""End-to-end tier: a real server thread + real test DB, hit over actual
HTTP (stdlib urllib, not self.client's in-process shortcut — no new
dependency) — proves the full login flow described in the auth-flow doc:
org signs up -> admin logs in -> admin adds a CSM -> CSM logs in with
their own credentials."""

import json
import urllib.error
import urllib.request

from django.test import LiveServerTestCase


def http_post(url, payload, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


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

        # 5. The CSM cannot add other CSMs — only the org admin can.
        csm_access = body["access"]
        status, body = http_post(
            self.api("/csms/"),
            {"name": "Someone Else", "email": "someone@acme.io", "password": "whatever12"},
            token=csm_access,
        )
        self.assertEqual(status, 403)
