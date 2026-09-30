"""End-to-end tier: real server, real HTTP, real test DB. A CSM builds an
organisation with an account, then adds a task, a note and a survey from the
account page, keyed by the account alone, and a note on the organisation
itself. The account's story holds the three account records only, filtered,
searched and paged, with the account's Needs attention. A peer cannot open
the account; the admin can, but never sees the CSM's personal note or task."""

from datetime import timedelta
from urllib.parse import urlencode

from django.test import LiveServerTestCase
from django.utils import timezone

from e2e.http import http_get, http_post


class AccountStoryFlowTests(LiveServerTestCase):
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

    def story(self, account_id, token, **query):
        suffix = f"?{urlencode(query)}" if query else ""
        return http_get(self.api(f"/accounts/{account_id}/story/{suffix}"), token=token)

    def kinds(self, account_id, token, **query):
        status, body = self.story(account_id, token, **query)
        self.assertEqual(status, 200, body)
        return [item["kind"] for item in body["items"]]

    def created(self, path, payload, token):
        status, body = http_post(self.api(path), payload, token=token)
        self.assertEqual(status, 201, body)
        return body

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

        # 2. Carl's organisation and its EMEA account, renewing in ten days.
        pizza = self.created("/customers/", {"name": "Pizza Hut"}, carl)["id"]
        renewal = (today + timedelta(days=10)).isoformat()
        emea = self.created(
            f"/customers/{pizza}/accounts/", {"name": "EMEA", "renewal_date": renewal}, carl
        )["id"]

        # 3. From the account page (no organisation id): an overdue task, a
        #    note and a survey. And a note on the organisation itself.
        self.created(
            f"/accounts/{emea}/tasks/",
            {
                "title": "Send the QBR deck",
                "due_date": (today - timedelta(days=1)).isoformat(),
                "priority": "high",
            },
            carl,
        )
        self.created(
            f"/accounts/{emea}/notes/",
            {"title": "Champion left", "body": "Sam moved on to Globex."},
            carl,
        )
        self.created(
            f"/accounts/{emea}/surveys/", {"survey_type": "nps", "sent_at": today.isoformat()}, carl
        )
        self.created(
            f"/customers/{pizza}/notes/", {"title": "Org plan", "body": "For everyone."}, carl
        )

        # 4. The account's story: the task (a timestamp) first, then today's
        #    survey and note (dates, ordered by kind). The organisation's own
        #    note is not the account's story.
        status, body = self.story(emea, carl)
        self.assertEqual(status, 200, body)
        self.assertEqual([item["kind"] for item in body["items"]], ["task", "survey", "note"])
        self.assertEqual(body["items"][0]["account"], {"id": emea, "name": "EMEA"})
        self.assertEqual(body["counts"]["by_account"], {"all": 3, "none": 0, str(emea): 3})
        self.assertEqual(
            body["counts"]["by_group"],
            {"all": 3, "conversations": 0, "tickets": 0, "tasks": 2, "feedback": 1, "health": 0},
        )
        self.assertEqual(
            body["attention"],
            {
                "renewal": {"date": renewal, "days": 10, "overdue": False},
                "tickets": None,
                "overdue_tasks": {"count": 1, "oldest_days": 1},
                "questions": None,
                "anomaly": None,
            },
        )

        # 5. A filter, a search, an ignored account chip, and paging.
        self.assertEqual(self.kinds(emea, carl, group="tasks"), ["task", "note"])
        self.assertEqual(self.kinds(emea, carl, q="globex"), ["note"])
        self.assertEqual(self.kinds(emea, carl, account="none"), ["task", "survey", "note"])
        seen, cursor = [], None
        while True:
            query = {"limit": 1, **({"cursor": cursor} if cursor else {})}
            status, body = self.story(emea, carl, **query)
            self.assertEqual(status, 200, body)
            seen += [item["kind"] for item in body["items"]]
            cursor = body["next_cursor"]
            if cursor is None:
                break
        self.assertEqual(seen, ["task", "survey", "note"])

        # 6. The tabs read back through the same account-keyed routes.
        status, body = http_get(self.api(f"/accounts/{emea}/tasks/"), token=carl)
        self.assertEqual(status, 200, body)
        self.assertEqual([task["title"] for task in body], ["Send the QBR deck"])

        # 7. Dana cannot open Carl's account, its story or its tabs. The admin
        #    can, but Carl's note and task are his (and his chain's) alone.
        status, _body = self.story(emea, dana)
        self.assertEqual(status, 404)
        status, _body = http_get(self.api(f"/accounts/{emea}/notes/"), token=dana)
        self.assertEqual(status, 404)
        status, body = self.story(emea, admin)
        self.assertEqual(status, 200, body)
        self.assertEqual([item["kind"] for item in body["items"]], ["survey"])
        self.assertIsNone(body["attention"]["overdue_tasks"])
