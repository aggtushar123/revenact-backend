"""End-to-end tier: real server, real HTTP, real test DB. A CSM builds an
organisation with an account and adds a task, a note and a survey through the
existing create endpoints, then reads them back as one story: filtered by
account, searched and paged, with the attention block. A peer cannot open the
organisation; the admin can, but never sees the CSM's personal note or task."""

from datetime import timedelta
from urllib.parse import urlencode

from django.test import LiveServerTestCase
from django.utils import timezone

from e2e.http import http_get, http_post


class OrganizationStoryFlowTests(LiveServerTestCase):
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

    def story(self, customer_id, token, **query):
        suffix = f"?{urlencode(query)}" if query else ""
        return http_get(self.api(f"/organizations/{customer_id}/story/{suffix}"), token=token)

    def kinds(self, customer_id, token, **query):
        status, body = self.story(customer_id, token, **query)
        self.assertEqual(status, 200, body)
        return [item["kind"] for item in body["items"]]

    def test_full_flow(self):
        today = timezone.localdate()

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

        # 2. Carl's organisation, renewing in ten days, with one account.
        status, body = http_post(
            self.api("/customers/"),
            {"name": "Pizza Hut", "renewal_date": (today + timedelta(days=10)).isoformat()},
            token=carl,
        )
        self.assertEqual(status, 201, body)
        pizza = body["id"]
        status, body = http_post(
            self.api(f"/customers/{pizza}/accounts/"), {"name": "EMEA"}, token=carl
        )
        self.assertEqual(status, 201, body)
        emea = body["id"]

        # 3. An overdue task on EMEA, a note and a survey on the organisation.
        status, body = http_post(
            self.api(f"/customers/{pizza}/accounts/{emea}/tasks/"),
            {
                "title": "Send the QBR deck",
                "due_date": (today - timedelta(days=1)).isoformat(),
                "priority": "high",
            },
            token=carl,
        )
        self.assertEqual(status, 201, body)
        status, body = http_post(
            self.api(f"/customers/{pizza}/notes/"),
            {"title": "Champion left", "body": "Sam moved on to Globex."},
            token=carl,
        )
        self.assertEqual(status, 201, body)
        status, body = http_post(
            self.api(f"/customers/{pizza}/surveys/"),
            {"survey_type": "nps", "sent_at": today.isoformat()},
            token=carl,
        )
        self.assertEqual(status, 201, body)

        # 4. Carl's story: the task (a timestamp) first, then today's survey and
        #    note (dates, ordered by kind); counts and attention over the lot.
        status, body = self.story(pizza, carl)
        self.assertEqual(status, 200, body)
        self.assertEqual([item["kind"] for item in body["items"]], ["task", "survey", "note"])
        self.assertEqual(body["items"][0]["account"], {"id": emea, "name": "EMEA"})
        self.assertIsNone(body["items"][2]["account"])
        self.assertEqual(body["counts"]["by_account"], {"all": 3, "none": 2, str(emea): 1})
        self.assertEqual(
            body["counts"]["by_group"],
            {"all": 3, "conversations": 0, "tickets": 0, "tasks": 2, "feedback": 1, "health": 0},
        )
        self.assertEqual(
            body["attention"]["renewal"],
            {"date": (today + timedelta(days=10)).isoformat(), "days": 10, "overdue": False},
        )
        self.assertEqual(body["attention"]["overdue_tasks"], {"count": 1, "oldest_days": 1})

        # 5. The account chip, the Organisation chip, a search and paging.
        self.assertEqual(self.kinds(pizza, carl, account=emea), ["task"])
        self.assertEqual(self.kinds(pizza, carl, account="none"), ["survey", "note"])
        self.assertEqual(self.kinds(pizza, carl, q="globex"), ["note"])
        seen, cursor = [], None
        while True:
            query = {"limit": 1, **({"cursor": cursor} if cursor else {})}
            status, body = self.story(pizza, carl, **query)
            self.assertEqual(status, 200, body)
            seen += [item["kind"] for item in body["items"]]
            cursor = body["next_cursor"]
            if cursor is None:
                break
        self.assertEqual(seen, ["task", "survey", "note"])

        # 6. Dana cannot open Carl's organisation. The admin can, but Carl's
        #    note and task are his (and his chain's) alone.
        status, _body = self.story(pizza, dana)
        self.assertEqual(status, 404)
        status, body = self.story(pizza, admin)
        self.assertEqual(status, 200, body)
        self.assertEqual([item["kind"] for item in body["items"]], ["survey"])
        self.assertEqual(body["counts"]["by_group"]["all"], 1)
        self.assertIsNone(body["attention"]["overdue_tasks"])
