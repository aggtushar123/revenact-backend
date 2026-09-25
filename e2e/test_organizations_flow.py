"""End-to-end tier: real server, real HTTP, real test DB. A CSM reads their
portfolio, never sees the admin's organization (not even by id), groups and
filters it, moves stages in bulk (the admin's id fails, theirs succeed),
archives one, and exports what is left as CSV."""

import csv
import io
import urllib.request

from django.test import LiveServerTestCase

from e2e.http import http_get, http_post


def http_get_text(url, token):
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request) as response:
        return response.status, response.headers.get("Content-Type"), response.read().decode()


class OrganizationsPortfolioFlowTests(LiveServerTestCase):
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

        # 2. The admin's own organization (owned by the admin) and two of Carl's.
        status, body = http_post(
            self.api("/customers/"),
            {"name": "Admin Co", "arr_billed_at_account": "90000"},
            token=admin,
        )
        self.assertEqual(status, 201, body)
        admin_co = body["id"]
        ids = {}
        for name, score, arr in (("Globex", "2.0", "50000"), ("Initech", "8.0", "20000")):
            status, body = http_post(
                self.api("/customers/"),
                {
                    "name": name,
                    "health_score": score,
                    "arr_billed_at_account": arr,
                    "lifecycle_stage": "live",
                },
                token=csm,
            )
            self.assertEqual(status, 201, body)
            ids[name] = body["id"]

        # 3. Carl's portfolio: his two, largest ARR first; the admin's is absent,
        #    even when named.
        status, body = http_get(self.api("/organizations/portfolio/"), token=csm)
        self.assertEqual(status, 200, body)
        self.assertEqual([row["name"] for row in body["results"]], ["Globex", "Initech"])
        self.assertEqual(body["summary"]["accounts"], 2)
        self.assertEqual(body["summary"]["arr"], 70000.0)
        status, body = http_get(self.api(f"/organizations/portfolio/?ids={admin_co}"), token=csm)
        self.assertEqual((status, body["count"]), (200, 0))

        # 4. Grouped by health: Poor first; filtered to Poor, the one risky row.
        status, body = http_get(self.api("/organizations/portfolio/?group=health"), token=csm)
        self.assertEqual([g["key"] for g in body["groups"]], ["poor", "good"])
        status, body = http_get(self.api("/organizations/portfolio/?health=poor"), token=csm)
        self.assertEqual([row["name"] for row in body["results"]], ["Globex"])
        self.assertEqual(body["results"][0]["signal"], {"kind": "risk", "label": "Risk 66"})

        # 5. Bulk stage change: Carl's two move, the admin's reports Not found.
        status, body = http_post(
            self.api("/organizations/bulk/"),
            {
                "ids": [ids["Globex"], ids["Initech"], admin_co],
                "action": "set_lifecycle",
                "value": "expansion",
            },
            token=csm,
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["updated"], [ids["Globex"], ids["Initech"]])
        self.assertEqual(body["failed"], [{"id": admin_co, "reason": "Not found."}])

        # 6. Archive Initech; it leaves the portfolio.
        status, body = http_post(
            self.api("/organizations/bulk/"),
            {"ids": [ids["Initech"]], "action": "archive"},
            token=csm,
        )
        self.assertEqual((status, body["updated"]), (200, [ids["Initech"]]))
        status, body = http_get(self.api("/organizations/portfolio/"), token=csm)
        self.assertEqual([row["name"] for row in body["results"]], ["Globex"])
        self.assertEqual(body["results"][0]["lifecycle"]["value"], "expansion")

        # 7. Export: a CSV of every field for what is left.
        status, content_type, text = http_get_text(
            self.api("/organizations/portfolio/export.csv"), csm
        )
        self.assertEqual(status, 200)
        self.assertTrue(content_type.startswith("text/csv"))
        table = list(csv.reader(io.StringIO(text)))
        self.assertEqual(len(table[0]), 35)
        self.assertEqual(table[0][0], "Organization")
        self.assertEqual(table[0][-1], "Currency")
        self.assertEqual([row[0] for row in table[1:]], ["Globex"])
