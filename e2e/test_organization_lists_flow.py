"""End-to-end tier: real server, real HTTP, real test DB. A CSM builds an
organisation with one account and logs records on both levels, then reads
the organisation page's lists: calls roll up with their account tags, the
survey list narrows to the organisation, and contacts, opportunities and
risks carry the account id the chips filter on. A peer who cannot open the
organisation gets a 404 on its calls and an empty survey list."""

from urllib.parse import urlencode

from django.test import LiveServerTestCase

from e2e.http import http_get, http_post


class OrganizationListsFlowTests(LiveServerTestCase):
    WHEN = "2026-09-20T10:00:00Z"

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

    def create(self, path, payload, token):
        status, body = http_post(self.api(path), payload, token=token)
        self.assertEqual(status, 201, body)
        return body

    def read(self, path, token):
        status, body = http_get(self.api(path), token=token)
        self.assertEqual(status, 200, body)
        return body

    def test_full_flow(self):
        # 1. An organisation signs up; its admin adds two CSMs, who log in.
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

        # 2. Carl's organisation with one account, and a second organisation.
        pizza = self.create("/customers/", {"name": "Pizza Hut"}, carl)["id"]
        emea = self.create(f"/customers/{pizza}/accounts/", {"name": "EMEA"}, carl)["id"]
        initech = self.create("/customers/", {"name": "Initech"}, carl)["id"]

        # 3. A call on each level. The organisation path stays organisation-level.
        call = self.create(
            f"/customers/{pizza}/calls/",
            {"title": "Kick-off", "occurred_at": self.WHEN, "summary": "Scope agreed."},
            carl,
        )
        self.assertEqual((call["account_id"], call["account_name"]), (None, None))
        call = self.create(
            f"/customers/{pizza}/accounts/{emea}/calls/",
            {"title": "EMEA QBR", "occurred_at": self.WHEN, "summary": "Renewal on track."},
            carl,
        )
        self.assertEqual((call["account_id"], call["account_name"]), (emea, "EMEA"))

        # 4. The organisation's Calls list rolls the account's call up, tagged.
        calls = self.read(f"/customers/{pizza}/calls/", carl)
        self.assertEqual(
            {c["title"]: (c["account_id"], c["account_name"]) for c in calls},
            {"Kick-off": (None, None), "EMEA QBR": (emea, "EMEA")},
        )
        self.assertEqual(self.read(f"/customers/{pizza}/files/", carl), [])

        # 5. Surveys on both levels and on another organisation; the filter narrows.
        self.create(
            f"/customers/{pizza}/surveys/", {"survey_type": "nps", "sent_at": "2026-09-01"}, carl
        )
        self.create(
            f"/customers/{pizza}/accounts/{emea}/surveys/",
            {"survey_type": "csat", "sent_at": "2026-09-02"},
            carl,
        )
        self.create(
            f"/customers/{initech}/surveys/", {"survey_type": "nps", "sent_at": "2026-09-03"}, carl
        )
        surveys = self.read("/surveys/?" + urlencode({"customer": pizza}), carl)
        self.assertEqual(
            {(s["survey_type"], s["account_id"]) for s in surveys}, {("nps", None), ("csat", emea)}
        )
        self.assertEqual(len(self.read("/surveys/", carl)), 3)

        # 6. People and Deals & risks rows carry the account id the chips use.
        self.create(
            f"/customers/{pizza}/accounts/{emea}/contacts/",
            {"name": "Sam Lee", "role": "champion", "email": "sam@pizza.io"},
            carl,
        )
        self.create(
            f"/customers/{pizza}/opportunities/",
            {"title": "Upsell", "mrr": "500.00", "stage": "discovery", "priority": "high"},
            carl,
        )
        self.create(
            f"/customers/{pizza}/accounts/{emea}/risks/",
            {"title": "Budget cut", "mrr": "200.00", "stage": "open", "priority": "medium"},
            carl,
        )
        contacts = self.read(f"/customers/{pizza}/contacts/", carl)
        self.assertEqual([(c["name"], c["account_id"]) for c in contacts], [("Sam Lee", emea)])
        opportunities = self.read(f"/customers/{pizza}/opportunities/", carl)
        self.assertEqual([(o["title"], o["account_id"]) for o in opportunities], [("Upsell", None)])
        risks = self.read(f"/customers/{pizza}/risks/", carl)
        self.assertEqual([(r["title"], r["account_id"]) for r in risks], [("Budget cut", emea)])

        # 7. Dana cannot open Carl's organisation: a 404, and no surveys by id.
        status, _ = http_get(self.api(f"/customers/{pizza}/calls/"), token=dana)
        self.assertEqual(status, 404)
        self.assertEqual(self.read("/surveys/?" + urlencode({"customer": pizza}), dana), [])
