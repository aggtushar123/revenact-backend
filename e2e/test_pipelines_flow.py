"""End-to-end tier: real server, real HTTP, real test DB. A CSM adds deals
and a risk with dates on their organisation and account, reads their
Pipelines book (never the admin's deal, not even by id), groups it by close
month, wins a deal by dragging it (the won-this-quarter tile counts it),
dates the rest in bulk (the admin's id fails), and exports what they see."""

import csv
import io
from datetime import timedelta

from django.test import LiveServerTestCase
from django.utils import timezone

from e2e.http import http_get, http_get_text, http_patch, http_post

EVERY_STAGE = (
    "discovery,qualification,solution_validation,proposal_price_review,"
    "negotiation,closed_won,closed_lost"
)


class PipelinesFlowTests(LiveServerTestCase):
    def api(self, path):
        return f"{self.live_server_url}/api/v1{path}"

    def test_full_flow(self):
        today = timezone.localdate()

        def day(n):
            return (today + timedelta(days=n)).isoformat()

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

        # 2. The admin's organisation and deal — undeparted, so only the
        #    parent rule keeps it from Carl.
        status, body = http_post(self.api("/customers/"), {"name": "Admin Co"}, token=admin)
        self.assertEqual(status, 201, body)
        status, body = http_post(
            self.api("/opportunities/"),
            {"customer_id": body["id"], "title": "Admin deal", "mrr": "9000", "department": ""},
            token=admin,
        )
        self.assertEqual(status, 201, body)
        admin_deal = body["id"]

        # 3. Carl's organisation and account, three deals and a risk.
        status, body = http_post(self.api("/customers/"), {"name": "Pizza Hut"}, token=csm)
        self.assertEqual(status, 201, body)
        pizza = body["id"]
        status, body = http_post(
            self.api(f"/customers/{pizza}/accounts/"), {"name": "Pizza EMEA"}, token=csm
        )
        self.assertEqual(status, 201, body)
        emea = body["id"]
        deals = {}
        for payload in (
            {"customer_id": pizza, "title": "Upsell", "mrr": "3000", "expected_close": day(-5)},
            {
                "account_id": emea,
                "title": "EMEA seats",
                "mrr": "2000",
                "expected_close": day(7),
                "priority": "high",
            },
            {"customer_id": pizza, "title": "Someday", "mrr": "1000"},
        ):
            status, body = http_post(self.api("/opportunities/"), payload, token=csm)
            self.assertEqual(status, 201, body)
            deals[body["title"]] = body["id"]
        status, body = http_post(
            self.api("/risks/"),
            {"customer_id": pizza, "title": "Budget cut", "mrr": "500", "due_by": day(20)},
            token=csm,
        )
        self.assertEqual((status, body["due_by"]), (201, day(20)))

        # 4. Carl's book: his three, largest MRR first; the tiles; the admin's
        #    deal is absent even when named.
        status, body = http_get(self.api("/pipelines/opportunities/"), token=csm)
        self.assertEqual(status, 200, body)
        self.assertEqual(
            [row["title"] for row in body["results"]], ["Upsell", "EMEA seats", "Someday"]
        )
        self.assertEqual(body["results"][0]["signal"], {"kind": "overdue", "label": "Overdue"})
        self.assertEqual(
            body["results"][1]["parent"], {"type": "account", "id": emea, "name": "Pizza EMEA"}
        )
        self.assertEqual(body["results"][1]["companies"], [{"id": pizza, "name": "Pizza Hut"}])
        self.assertEqual(body["summary"]["open"], {"count": 3, "mrr": 6000.0})
        self.assertEqual(body["summary"]["overdue"], {"count": 1, "mrr": 3000.0})
        self.assertEqual(body["summary"]["within"]["30"], {"count": 1, "mrr": 2000.0})
        status, body = http_get(self.api(f"/pipelines/opportunities/?ids={admin_deal}"), token=csm)
        self.assertEqual((status, body["count"]), (200, 0))
        status, body = http_get(self.api("/pipelines/risks/"), token=csm)
        self.assertEqual([row["title"] for row in body["results"]], ["Budget cut"])

        # 5. Grouped by close month: Overdue first, No date last.
        status, body = http_get(self.api("/pipelines/opportunities/?group=month"), token=csm)
        keys = [group["key"] for group in body["groups"]]
        self.assertEqual((keys[0], keys[-1]), ("overdue", "none"))

        # 6. Drag Upsell to Closed Won: it leaves the open list and the won
        #    tile counts it.
        status, body = http_patch(
            self.api(f"/opportunities/{deals['Upsell']}/"), {"stage": "closed_won"}, token=csm
        )
        self.assertEqual(status, 200, body)
        status, body = http_get(self.api("/pipelines/opportunities/"), token=csm)
        self.assertEqual(body["count"], 2)
        self.assertEqual(
            body["summary"]["done_this_quarter"],
            {"stage": "closed_won", "count": 1, "mrr": 3000.0},
        )
        self.assertEqual(body["summary"]["overdue"]["count"], 0)

        # 7. Bulk date: Carl's two are dated, the admin's reports Not found.
        status, body = http_post(
            self.api("/pipelines/opportunities/bulk/"),
            {
                "ids": [deals["Someday"], deals["EMEA seats"], admin_deal],
                "action": "set_date",
                "value": day(45),
            },
            token=csm,
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["updated"], [deals["Someday"], deals["EMEA seats"]])
        self.assertEqual(body["failed"], [{"id": admin_deal, "reason": "Not found."}])
        status, body = http_get(self.api("/pipelines/opportunities/?date=90"), token=csm)
        self.assertEqual(sorted(row["title"] for row in body["results"]), ["EMEA seats", "Someday"])

        # 8. Export: every field for what Carl sees, closed stages opted in.
        status, content_type, text = http_get_text(
            self.api(f"/pipelines/opportunities/export.csv?stage={EVERY_STAGE}"), csm
        )
        self.assertEqual(status, 200)
        self.assertTrue(content_type.startswith("text/csv"))
        table = list(csv.reader(io.StringIO(text)))
        self.assertEqual(len(table[0]), 13)
        self.assertEqual((table[0][0], table[0][-1]), ("Title", "Currency"))
        self.assertEqual(sorted(row[0] for row in table[1:]), ["EMEA seats", "Someday", "Upsell"])
