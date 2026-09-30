from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from services.accounts.models import User
from services.customers.models import Task, Ticket
from services.organizations.tests.test_story_views import PER_TYPE

from .fixtures import AccountStoryFixture


def story_url(account_id):
    return f"/api/v1/accounts/{account_id}/story/"


def client_for(user):
    api = APIClient()
    api.force_authenticate(user)
    return api


class AccountStoryMatchesPerTypeEndpointsTests(AccountStoryFixture):
    def per_type(self, user, account):
        api = client_for(user)
        found = set()
        for kind, path in PER_TYPE.items():
            url = f"/api/v1/customers/{self.pizza.pk}/accounts/{account.pk}/{path}/"
            response = api.get(url)
            self.assertEqual(response.status_code, 200, url)
            found |= {(kind, row["id"]) for row in response.data}
        return found

    def story_keys(self, user, account):
        api = client_for(user)
        seen, cursor = [], None
        while True:
            query = {"limit": "3", **({"cursor": cursor} if cursor else {})}
            body = api.get(story_url(account.pk), query).data
            seen += [(item["kind"], item["id"]) for item in body["items"]]
            cursor = body["next_cursor"]
            if cursor is None:
                break
        self.assertEqual(len(seen), len(set(seen)))
        return {key for key in seen if key[0] != "health"}

    def test_the_story_is_what_the_per_type_endpoints_show_the_same_viewer(self):
        for parent in (self.pizza, self.emea, self.apac):
            for kind in PER_TYPE:
                self.make(kind, parent)
        self.email(self.emea, mailbox_owner=self.other)
        self.email(self.emea, mailbox_owner=self.csm)
        self.note(self.emea, author=self.other)
        self.task(self.apac, created_by=self.other, assignee=self.other)
        self.ticket(self.emea, department="engineering")
        for user in (self.csm, self.admin):
            for account in (self.emea, self.apac):
                with self.subTest(user=user.name, account=account.name):
                    expected = self.per_type(user, account)
                    self.assertEqual(self.story_keys(user, account), expected)
                    self.assertGreaterEqual(len(expected), 8)


class AccountStoryQueryCountTests(AccountStoryFixture):
    """The page, the counts and the attention block cost a fixed number of
    queries. A per-row query anywhere would make the count grow with the
    account, which the two-size test catches.

    Twenty-four, for an admin or a CSM, with a fresh user per request as a
    real request loads one:
      1. `user.organisation` (`visible_accounts`' own
         `customers__organisation=user.organisation`), a real FK fetch
      2. the caller's membership (`sees_everything`, memoised on the user)
      3. `user.role` (`capabilities_for` reads the column)
      4. the account (`resolve_account_scope`, get_object_or_404)
      5. the org chart below the caller (`subtree_ids`), walked once and
         memoised: `visible_accounts` (a CSM) and the mail, note and task
         rules all reuse it
      6. the health snapshots
      7-14. one page query per record source (activity, calendar_event,
            call, email, note, survey, task, ticket), related rows joined
      15-22. one count aggregate per record source
      23. attention: urgent tickets (one aggregate)
      24. attention: overdue tasks (one aggregate)
    The organisation story's 28 minus its organisation and accounts lookups
    (2) plus this account (1), minus open questions (2) and the anomaly (1).

    If the pinned number is ever wrong, print `[q["sql"] for q in
    ctx.captured_queries]`: every query must be one of these kinds; an extra
    one is a bug to fix, not a number to bump."""

    EXPECTED = 24

    def setUp(self):
        super().setUp()
        self.next_day = 30

    def book(self, size):
        for _ in range(size):
            for kind in PER_TYPE:
                self.make(kind, self.emea)
            self.snapshot(self.emea, self.days_ago(self.next_day + 1), "8.0")
            self.snapshot(self.emea, self.days_ago(self.next_day), "3.0")
            self.next_day += 2
        # Content for every computed entry of `attention`: the aggregates run
        # whether or not a row matches, so this must not move the count.
        self.ticket(self.emea, priority=Ticket.Priority.HIGH, day=self.days_ago(2))
        self.task(self.emea, due=self.days_ago(2), status=Task.Status.PENDING)

    def count(self, user=None, **query):
        api = client_for(User.objects.get(pk=(user or self.admin).pk))
        with CaptureQueriesContext(connection) as ctx:
            response = api.get(story_url(self.emea.pk), query)
        self.assertEqual(response.status_code, 200)
        return len(ctx.captured_queries)

    def test_the_count_is_pinned_and_does_not_grow_with_the_account(self):
        self.book(3)
        small = self.count()
        self.book(12)
        self.assertEqual(self.count(), small)
        self.assertEqual(small, self.EXPECTED)

    def test_a_csm_s_count_does_not_grow_either(self):
        self.book(3)
        small = self.count(self.csm)
        self.book(12)
        self.assertEqual(self.count(self.csm), small)
        self.assertEqual(small, self.EXPECTED)

    def test_the_next_page_costs_the_same(self):
        self.book(5)
        cursor = (
            client_for(self.admin).get(story_url(self.emea.pk), {"limit": "5"}).data["next_cursor"]
        )
        self.assertIsNotNone(cursor)
        self.assertEqual(self.count(limit="5", cursor=cursor), self.count(limit="5"))

    def test_filters_never_add_queries(self):
        self.book(5)
        plain = self.count()
        for query in (
            {"group": "tickets"},
            {"source": "email,note"},
            {"account": "none"},
            {"q": "renewal"},
            {"thread": "t-1"},
        ):
            with self.subTest(**query):
                self.assertLessEqual(self.count(**query), plain)
