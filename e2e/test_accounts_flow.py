"""End-to-end tier: real server, real HTTP, real test DB. A CSM reads their
accounts portfolio, never sees the admin's account (not even by id), groups
and filters it, moves stages in bulk (the admin's id fails, theirs
succeed), unassigns one, and exports what they see as CSV."""

import csv
import io
import urllib.request

from django.test import LiveServerTestCase

from e2e.http import http_get, http_post


def http_get_text(url, token):
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request) as response:
        return response.status, response.headers.get("Content-Type"), response.read().decode()


class AccountsPortfolioFlowTests(LiveServerTestCase):
    def api(self, path):
        return f"{self.live_server_url}/api/v1{path}"

    def test_full_flow(self):
        # 1. An organisation signs up; its admin adds a CSM, who logs in.
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
            self.api("/auth/login/"), {"email": "carl@acme.io", "password": "csmpassword1"}
        )
        self.assertEqual(status, 200, body)
        csm = body["access"]

        # 2. The admin's organisation and account; Carl's organisation and two accounts.
        status, body = http_post(self.api("/customers/"), {"name": "Admin Co"}, token=admin)
        self.assertEqual(status, 201, body)
        status, body = http_post(
            self.api(f"/customers/{body['id']}/accounts/"),
            {"name": "Admin Division", "arr": "90000"},
            token=admin,
        )
        self.assertEqual(status, 201, body)
        admin_division = body["id"]
        status, body = http_post(self.api("/customers/"), {"name": "Pizza Hut"}, token=csm)
        self.assertEqual(status, 201, body)
        pizza = body["id"]
        ids = {}
        for name, score, arr in (("Pizza EMEA", "2.0", "50000"), ("Pizza APAC", "8.0", "20000")):
            status, body = http_post(
                self.api(f"/customers/{pizza}/accounts/"),
                {"name": name, "health_score": score, "arr": arr, "lifecycle_stage": "live"},
                token=csm,
            )
            self.assertEqual(status, 201, body)
            ids[name] = body["id"]

        # 3. Carl's portfolio: his two, largest ARR first, each naming Pizza Hut;
        #    the admin's account is absent, even when named.
        status, body = http_get(self.api("/accounts/portfolio/"), token=csm)
        self.assertEqual(status, 200, body)
        self.assertEqual([row["name"] for row in body["results"]], ["Pizza EMEA", "Pizza APAC"])
        self.assertEqual(body["results"][0]["organisation"], {"id": pizza, "name": "Pizza Hut"})
        self.assertEqual(body["summary"]["accounts"], 2)
        self.assertEqual(body["summary"]["arr"], 70000.0)
        self.assertEqual(body["currency"], "USD")
        status, body = http_get(self.api(f"/accounts/portfolio/?ids={admin_division}"), token=csm)
        self.assertEqual((status, body["count"]), (200, 0))

        # 4. Grouped by health: Poor first; filtered to Poor, the one risky row.
        status, body = http_get(self.api("/accounts/portfolio/?group=health"), token=csm)
        self.assertEqual([g["key"] for g in body["groups"]], ["poor", "good"])
        status, body = http_get(self.api("/accounts/portfolio/?health=poor"), token=csm)
        self.assertEqual([row["name"] for row in body["results"]], ["Pizza EMEA"])
        self.assertEqual(body["results"][0]["signal"], {"kind": "risk", "label": "Risk 66"})

        # 5. Bulk stage change: Carl's two move, the admin's reports Not found.
        status, body = http_post(
            self.api("/accounts/bulk/"),
            {
                "ids": [ids["Pizza EMEA"], ids["Pizza APAC"], admin_division],
                "action": "set_lifecycle",
                "value": "expansion",
            },
            token=csm,
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["updated"], [ids["Pizza EMEA"], ids["Pizza APAC"]])
        self.assertEqual(body["failed"], [{"id": admin_division, "reason": "Not found."}])

        # 6. Unassign APAC; the Unassigned filter finds it, in its new stage.
        status, body = http_post(
            self.api("/accounts/bulk/"),
            {"ids": [ids["Pizza APAC"]], "action": "set_owner", "value": None},
            token=csm,
        )
        self.assertEqual((status, body["updated"]), (200, [ids["Pizza APAC"]]))
        status, body = http_get(self.api("/accounts/portfolio/?owner=unassigned"), token=csm)
        self.assertEqual([row["name"] for row in body["results"]], ["Pizza APAC"])
        self.assertEqual(body["results"][0]["lifecycle"]["value"], "expansion")

        # 7. Export: a CSV of every field for what Carl sees.
        status, content_type, text = http_get_text(self.api("/accounts/portfolio/export.csv"), csm)
        self.assertEqual(status, 200)
        self.assertTrue(content_type.startswith("text/csv"))
        table = list(csv.reader(io.StringIO(text)))
        self.assertEqual(len(table[0]), 25)
        self.assertEqual((table[0][0], table[0][-1]), ("Account", "Currency"))
        self.assertEqual([row[0] for row in table[1:]], ["Pizza EMEA", "Pizza APAC"])
