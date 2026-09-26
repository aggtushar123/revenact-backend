from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from services.accounts.models import User
from services.anomalies.models import Anomaly, AnomalyEvidence
from services.customers.models import Ticket
from services.knowledge.models import Question

from .story_fixtures import StoryFixture

#: Each record kind and its per-type endpoint under /customers/<id>/ and
#: /customers/<id>/accounts/<account_id>/.
PER_TYPE = {
    "activity": "activities",
    "calendar_event": "calendar-events",
    "call": "calls",
    "email": "emails",
    "note": "notes",
    "survey": "surveys",
    "task": "tasks",
    "ticket": "tickets",
}


def story_url(customer_id):
    return f"/api/v1/organizations/{customer_id}/story/"


class StoryEndpointFixture(StoryFixture):
    @staticmethod
    def client_for(user):
        api = APIClient()
        api.force_authenticate(user)
        return api

    def get(self, user=None, customer=None, **query):
        api = self.client_for(user or self.csm)
        response = api.get(story_url((customer or self.pizza).pk), query)
        self.assertEqual(response.status_code, 200, response.content)
        return response.data


class StoryEndpointTests(StoryEndpointFixture):
    def test_requires_authentication(self):
        self.assertEqual(APIClient().get(story_url(self.pizza.pk)).status_code, 401)

    def test_an_organisation_the_viewer_cannot_open_is_a_404(self):
        danas = self.customer("Dana's", owner=self.other)
        globex = self.customer("Globex's", owner=None, organisation=self.other_org)
        api = self.client_for(self.csm)
        for customer_id in (danas.pk, globex.pk, 999_999):
            self.assertEqual(api.get(story_url(customer_id)).status_code, 404, customer_id)

    def test_an_archived_organisation_still_opens(self):
        gone = self.customer("Gone", is_archived=True)
        self.note(gone)
        self.assertEqual(len(self.get(customer=gone)["items"]), 1)

    def test_response_shape(self):
        self.note(self.emea)
        body = self.get()
        self.assertEqual(set(body), {"items", "next_cursor", "counts", "attention"})
        self.assertEqual(
            set(body["items"][0]),
            {
                "id",
                "kind",
                "source",
                "occurred_at",
                "all_day",
                "account",
                "title",
                "summary",
                "actor",
                "link",
            },
        )
        self.assertEqual(set(body["items"][0]["link"]), {"thread_id", "url"})
        self.assertEqual(set(body["counts"]), {"by_group", "by_kind", "by_account"})
        self.assertEqual(
            set(body["attention"]),
            {"renewal", "tickets", "overdue_tasks", "questions", "anomaly"},
        )
        self.assertIsNone(body["next_cursor"])

    def test_unknown_values_are_ignored_not_rejected(self):
        self.note(self.pizza)
        body = self.get(group="mood", source="slack", account="emea", limit="lots", cursor="%%%")
        self.assertEqual(len(body["items"]), 1)
        self.assertEqual(body["counts"]["by_group"]["all"], 1)


class StoryMatchesPerTypeEndpointsTests(StoryEndpointFixture):
    def per_type(self, user):
        api = self.client_for(user)
        found = set()
        for kind, path in PER_TYPE.items():
            urls = [f"/api/v1/customers/{self.pizza.pk}/{path}/"] + [
                f"/api/v1/customers/{self.pizza.pk}/accounts/{account.pk}/{path}/"
                for account in (self.emea, self.apac)
            ]
            for url in urls:
                response = api.get(url)
                self.assertEqual(response.status_code, 200, url)
                found |= {(kind, row["id"]) for row in response.data}
        return found

    def story_keys(self, user):
        api = self.client_for(user)
        seen, cursor = [], None
        while True:
            query = {"limit": "3", **({"cursor": cursor} if cursor else {})}
            body = api.get(story_url(self.pizza.pk), query).data
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
        self.email(self.pizza, mailbox_owner=self.csm)
        self.note(self.pizza, author=self.other)
        self.task(self.apac, created_by=self.other, assignee=self.other)
        self.ticket(self.pizza, department="engineering")
        for user in (self.csm, self.admin):
            with self.subTest(user=user.name):
                expected = self.per_type(user)
                self.assertEqual(self.story_keys(user), expected)
                self.assertGreaterEqual(len(expected), 24)


class StoryPrivacyTests(StoryEndpointFixture):
    def test_records_the_viewer_may_not_read_reach_no_item_count_attention_or_search(self):
        self.email(self.emea, mailbox_owner=self.other, subject="Secret pricing", body="Secret")
        self.note(self.pizza, author=self.other, title="Secret note", body="Secret pricing")
        self.ticket(
            self.pizza,
            department="engineering",
            title="Secret pricing bug",
            priority=Ticket.Priority.CRITICAL,
        )
        self.task(
            self.pizza,
            title="Secret pricing task",
            created_by=self.other,
            assignee=self.other,
            due=self.days_ago(3),
        )
        body = self.get()
        self.assertEqual(body["items"], [])
        self.assertEqual(body["counts"]["by_group"]["all"], 0)
        self.assertEqual(set(body["counts"]["by_kind"].values()), {0})
        self.assertEqual(set(body["counts"]["by_account"].values()), {0})
        self.assertIsNone(body["attention"]["tickets"])
        self.assertIsNone(body["attention"]["overdue_tasks"])
        searched = self.get(q="secret")
        self.assertEqual((searched["items"], searched["counts"]["by_group"]["all"]), ([], 0))
        # Readable to Leadership, and then search finds it: the rule, not the search, hid it.
        leadership = self.get(self.admin, q="secret")
        self.assertEqual([item["kind"] for item in leadership["items"]], ["ticket"])

    def test_only_records_linked_to_this_organisation(self):
        taco = self.customer("Taco Co")
        shared = self.account("Shared div", customers=[self.pizza, taco])
        taco_div = self.account("Taco div", customers=[taco])
        on_shared = self.note(shared)
        on_taco = self.note(taco)
        on_taco_div = self.note(taco_div)
        self.assertEqual(
            [(item["kind"], item["id"]) for item in self.get()["items"]],
            [("note", on_shared.pk)],
        )
        self.assertEqual(self.get(account=str(taco_div.pk))["items"], [])
        self.assertEqual(
            {item["id"] for item in self.get(customer=taco)["items"]},
            {on_shared.pk, on_taco.pk, on_taco_div.pk},
        )


class StoryQueryCountTests(StoryEndpointFixture):
    """The page, the counts and the attention block cost a fixed number of
    queries. A per-row query anywhere would make the count grow with the book,
    which the two-size test catches.

    Thirty-three for an admin, who sees everything (so no org-chart lookup in
    `visible_customers` itself):
      1. `user.organisation` (`visible_customers`'s own `Customer.objects
         .filter(organisation=user.organisation)`) — a real FK fetch, because
         `count()` below uses a freshly loaded `User.objects.get(...)` per
         call, the way a real request's authenticated user is loaded, rather
         than a warm, reused instance.
      2. the caller's membership (`services.identity.context`, memoised on
         the user for the rest of the request — one query even though it is
         asked for repeatedly, because `sees_everything` and the attention
         block's own capability checks all share the same cached lookup)
      3. `user.role` (`capabilities_for` reads permissions from the column,
         deliberately, not from `membership.role` — see its docstring — so
         this is a second FK fetch independent of query 2's join)
      4. the organisation (`visible_customers`, get_object_or_404)
      5. its accounts the caller may open (`visible_accounts`)
      6-8. the org chart below the caller, once each as the mail, note and task
           rules are built (`chain_visible_q`, `visible_tasks`)
      9. the health snapshots
      10-17. one page query per record source (activity, calendar_event, call,
            email, note, survey, task, ticket), related rows joined
      18-25. one count aggregate per record source
      26. attention: urgent tickets (one aggregate)
      27. attention: overdue tasks (one aggregate)
      28-31. attention: open questions: `scope_ids`'s own org chart and
             function-mates queries, `visible_questions`'s separate
             `subtree_ids` call, then the count
      32-33. attention: the evidence rule's org chart (`readable_evidence_q`),
             then the latest readable evidence with its anomaly

    This is 31 plus the two queries the view itself adds (1 and 3 above):
    `build_story`'s own pinned test (`test_story_build.py`) warms the
    membership/organisation/role cache with a throwaway call on a *reused*
    user instance first, so it never pays them. A real request — and this
    test's fresh `User.objects.get(...)` per call — always does. The number
    stays fixed across book sizes and viewers either way, which is what this
    test actually guards.

    If the pinned number is ever wrong, print `[q["sql"] for q in
    ctx.captured_queries]`: every query must be one of these kinds; an extra
    one is a bug to fix, not a number to bump.
    """

    EXPECTED = 33

    def book(self, size):
        for i in range(size):
            account = self.account(f"Div {size}-{i}")
            for kind in PER_TYPE:
                self.make(kind, account)
            self.snapshot(account, self.days_ago(70), "8.0")
            self.snapshot(account, self.days_ago(40), "3.0")
        Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.csm,
            assignee=self.engineer,
            text=f"Why {size}?",
        )
        now = timezone.now()
        anomaly = Anomaly.objects.create(
            organisation=self.org, title=f"Fault {size}", first_seen_at=now, last_seen_at=now
        )
        AnomalyEvidence.objects.create(
            anomaly=anomaly,
            organisation=self.org,
            kind=AnomalyEvidence.Kind.CALL,
            record_id=size,
            customer=self.pizza,
            snippet="fault",
            occurred_at=now,
        )

    def count(self, user=None, **query):
        # A fresh user each time, as a real request loads one: memoised lookups
        # cached on a reused instance would otherwise flatter the second call.
        api = self.client_for(User.objects.get(pk=(user or self.admin).pk))
        with CaptureQueriesContext(connection) as ctx:
            response = api.get(story_url(self.pizza.pk), query)
        self.assertEqual(response.status_code, 200)
        return len(ctx.captured_queries)

    def test_the_count_is_pinned_and_does_not_grow_with_the_book(self):
        self.book(3)
        small = self.count()
        self.book(12)
        self.assertEqual(self.count(), small)
        self.assertEqual(small, self.EXPECTED)

    def test_a_csm_s_count_does_not_grow_with_the_book_either(self):
        self.book(3)
        small = self.count(self.csm)
        self.book(12)
        self.assertEqual(self.count(self.csm), small)

    def test_the_next_page_costs_the_same(self):
        self.book(5)
        api = self.client_for(self.admin)
        cursor = api.get(story_url(self.pizza.pk), {"limit": "5"}).data["next_cursor"]
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
