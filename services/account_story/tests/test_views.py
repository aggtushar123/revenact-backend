from datetime import timedelta

from django.utils import timezone
from rest_framework.test import APIClient

from services.accounts.models import User
from services.customers.models import Ticket
from services.customers.tests.test_views import blind_to_one_account

from .fixtures import AccountStoryFixture


def story_url(account_id):
    return f"/api/v1/accounts/{account_id}/story/"


class AccountStoryEndpointFixture(AccountStoryFixture):
    @staticmethod
    def client_for(user):
        api = APIClient()
        api.force_authenticate(user)
        return api

    def get(self, user=None, account=None, **query):
        api = self.client_for(user or self.csm)
        response = api.get(story_url((account or self.emea).pk), query)
        self.assertEqual(response.status_code, 200, response.content)
        return response.data

    def ids(self, user=None, account=None, **query):
        return set(self.keys(self.get(user, account, **query)))


class AccountStoryEndpointTests(AccountStoryEndpointFixture):
    def test_requires_authentication(self):
        self.assertEqual(APIClient().get(story_url(self.emea.pk)).status_code, 401)

    def test_post_is_not_allowed(self):
        api = self.client_for(self.csm)
        self.assertEqual(api.post(story_url(self.emea.pk), {}).status_code, 405)

    def test_an_account_the_viewer_cannot_open_is_a_404_whether_or_not_it_exists(self):
        danas_co = self.customer("Dana's", owner=self.other)
        danas = self.account("Dana's div", customers=[danas_co], owner=self.other)
        globex = self.customer("Globex's", owner=None, organisation=self.other_org)
        stranger = self.account("Stranger div", customers=[globex])
        self.note(danas)
        self.note(stranger)
        api = self.client_for(self.csm)
        for account_id in (danas.pk, stranger.pk, 999_999):
            with self.subTest(account_id=account_id):
                self.assertEqual(api.get(story_url(account_id)).status_code, 404)

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
        self.assertEqual(set(body["counts"]), {"by_group", "by_kind", "by_account"})
        self.assertEqual(set(body["counts"]["by_account"]), {"all", "none", str(self.emea.pk)})
        self.assertEqual(
            set(body["attention"]),
            {"renewal", "tickets", "overdue_tasks", "questions", "anomaly"},
        )
        self.assertIsNone(body["next_cursor"])

    def test_only_records_filed_on_this_account(self):
        taco = self.customer("Taco Co")
        shared = self.account("Shared div", customers=[self.pizza, taco])
        self.note(self.pizza)
        self.note(self.apac)
        self.note(taco)
        on_emea = self.note(self.emea)
        on_shared = self.note(shared)
        self.snapshot(self.pizza, self.days_ago(70), "8.0")
        self.snapshot(self.pizza, self.days_ago(40), "3.0")
        self.snapshot(self.emea, self.days_ago(70), "8.0")
        fell = self.snapshot(self.emea, self.days_ago(40), "3.0")
        body = self.get()
        self.assertEqual(self.keys(body), [("note", on_emea.pk), ("health", fell.pk)])
        self.assertEqual(body["items"][0]["account"], {"id": self.emea.pk, "name": "EMEA"})
        self.assertEqual(body["counts"]["by_account"], {"all": 2, "none": 0, str(self.emea.pk): 2})
        self.assertEqual(self.keys(self.get(account=shared)), [("note", on_shared.pk)])

    def test_the_account_parameter_is_ignored(self):
        on_emea = self.note(self.emea)
        self.note(self.apac)
        for value in (str(self.apac.pk), "none", "abc"):
            with self.subTest(account=value):
                api = self.client_for(self.csm)
                body = api.get(story_url(self.emea.pk), {"account": value}).data
                self.assertEqual(self.keys(body), [("note", on_emea.pk)])
                self.assertEqual(body["counts"]["by_group"]["all"], 1)

    def test_unknown_values_are_ignored_not_rejected(self):
        self.note(self.emea)
        body = self.get(group="mood", source="slack", limit="lots", cursor="%%%")
        self.assertEqual(len(body["items"]), 1)
        self.assertEqual(body["counts"]["by_group"]["all"], 1)

    def test_group_source_search_thread_and_paging(self):
        now = timezone.now()
        first = self.email(self.emea, thread_id="t-1", at=now - timedelta(minutes=5))
        self.email(self.emea, subject="Other", body="Hello", thread_id="t-2", at=now)
        note = self.note(self.emea, title="SSO blocker", body="SSO is blocking the rollout")
        self.assertEqual(self.ids(group="tasks"), {("note", note.pk)})
        self.assertEqual(self.ids(source="note"), {("note", note.pk)})
        self.assertEqual(self.ids(q="sso"), {("note", note.pk)})
        self.assertEqual(self.ids(thread="t-1"), {("email", first.pk)})
        everything = self.keys(self.get())
        seen, cursor = [], None
        while True:
            body = self.get(limit="1", **({"cursor": cursor} if cursor else {}))
            seen += self.keys(body)
            cursor = body["next_cursor"]
            if cursor is None:
                break
        self.assertEqual(seen, everything)
        self.assertEqual(len(seen), 3)

    def test_a_cursor_from_the_organisation_story_reads_the_first_page(self):
        for _ in range(3):
            self.note(self.emea)
        api = self.client_for(self.csm)
        org_cursor = api.get(f"/api/v1/organizations/{self.pizza.pk}/story/", {"limit": "1"}).data[
            "next_cursor"
        ]
        self.assertIsNotNone(org_cursor)
        first = self.get(limit="1")
        self.assertEqual(self.keys(self.get(limit="1", cursor=org_cursor)), self.keys(first))


class AccountStoryPrivacyTests(AccountStoryEndpointFixture):
    def test_records_the_viewer_may_not_read_reach_no_item_count_attention_or_search(self):
        self.email(self.emea, mailbox_owner=self.other, subject="Secret pricing", body="Secret")
        self.note(self.emea, author=self.other, title="Secret note", body="Secret pricing")
        self.ticket(
            self.emea,
            department="engineering",
            title="Secret pricing bug",
            priority=Ticket.Priority.CRITICAL,
        )
        self.task(
            self.emea,
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

    def test_mail_is_its_mailbox_owner_s_and_their_chain_s(self):
        User.objects.filter(pk=self.other.pk).update(reports_to=self.csm)
        peer = User.objects.create_user(
            email="pat@acme.io",
            password="supersecret1",
            name="Pat CSM",
            organisation=self.org,
            role=User.Role.CSM,
        )
        now = timezone.now()
        mine = self.email(self.emea, mailbox_owner=self.csm, at=now - timedelta(minutes=1))
        report_s = self.email(self.emea, mailbox_owner=self.other, at=now - timedelta(minutes=2))
        self.email(self.emea, mailbox_owner=peer, at=now - timedelta(minutes=3))
        unowned = self.email(self.emea, at=now - timedelta(minutes=4))
        carl = User.objects.get(pk=self.csm.pk)
        dana = User.objects.get(pk=self.other.pk)
        self.assertEqual(
            self.ids(carl),
            {("email", mine.pk), ("email", report_s.pk), ("email", unowned.pk)},
        )
        self.assertEqual(self.ids(dana), {("email", report_s.pk), ("email", unowned.pk)})

    def test_tickets_are_read_by_department(self):
        cs = self.ticket(self.emea, number="T-1", department="cs")
        eng = self.ticket(self.emea, number="T-2", department="engineering")
        anyone = self.ticket(self.emea, number="T-3")
        self.assertEqual(self.ids(), {("ticket", cs.pk), ("ticket", anyone.pk)})
        self.assertEqual(self.ids(self.engineer), {("ticket", eng.pk), ("ticket", anyone.pk)})
        self.assertEqual(
            self.ids(self.admin), {("ticket", cs.pk), ("ticket", eng.pk), ("ticket", anyone.pk)}
        )

    def test_notes_and_tasks_follow_their_personal_rules(self):
        mine_note = self.note(self.emea, author=self.csm)
        self.note(self.emea, author=self.other)
        mine_task = self.task(self.emea, created_by=self.csm, assignee=self.csm)
        assigned = self.task(self.emea, created_by=self.other, assignee=self.csm)
        self.task(self.emea, created_by=self.other, assignee=self.other)
        self.assertEqual(
            self.ids(),
            {("note", mine_note.pk), ("task", mine_task.pk), ("task", assigned.pk)},
        )
        # Alice sees every account, but is in neither person's chain.
        self.assertEqual(self.ids(self.admin), set())

    def test_blind_to_one_account(self):
        viewer, seen, hidden = blind_to_one_account(self.pizza)
        on_seen = self.note(seen)
        self.note(hidden)
        self.ticket(hidden, priority=Ticket.Priority.CRITICAL)
        body = self.get(viewer, seen)
        self.assertEqual(self.keys(body), [("note", on_seen.pk)])
        self.assertEqual(body["counts"]["by_group"]["all"], 1)
        self.assertIsNone(body["attention"]["tickets"])
        self.assertEqual(self.client_for(viewer).get(story_url(hidden.pk)).status_code, 404)

    def test_another_tenant_s_account_is_a_404_even_for_an_admin(self):
        globex = self.customer("Globex's", owner=None, organisation=self.other_org)
        stranger = self.account("Stranger div", customers=[globex])
        self.note(stranger)
        for user in (self.csm, self.admin):
            with self.subTest(user=user.name):
                response = self.client_for(user).get(story_url(stranger.pk))
                self.assertEqual(response.status_code, 404)
