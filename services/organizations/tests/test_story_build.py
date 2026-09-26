from datetime import timedelta

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from services.anomalies.models import Anomaly, AnomalyEvidence
from services.customers.models import Ticket
from services.knowledge.models import Question

from .story_fixtures import StoryFixture


class StoryOrderTests(StoryFixture):
    def test_newest_first_across_sources(self):
        email = self.email(self.pizza, at=timezone.now() - timedelta(hours=1))
        activity = self.activity(self.emea, day=self.days_ago(1))
        ticket = self.ticket(self.apac, day=self.days_ago(2))
        note = self.note(self.pizza, day=self.days_ago(3))
        self.snapshot(self.pizza, self.days_ago(70), "8.0")
        fell = self.snapshot(self.pizza, self.days_ago(40), "3.0")
        self.assertEqual(
            self.keys(self.story()),
            [
                ("email", email.pk),
                ("activity", activity.pk),
                ("ticket", ticket.pk),
                ("note", note.pk),
                ("health", fell.pk),
            ],
        )

    def test_one_moment_orders_by_kind_then_id_descending(self):
        day = self.days_ago(1)
        first = self.activity(self.pizza, day=day)
        second = self.activity(self.pizza, day=day)
        note = self.note(self.pizza, day=day)
        survey = self.survey(self.pizza, day=day)
        ticket = self.ticket(self.pizza, day=day)
        self.assertEqual(
            self.keys(self.story()),
            [
                ("ticket", ticket.pk),
                ("survey", survey.pk),
                ("note", note.pk),
                ("activity", second.pk),
                ("activity", first.pk),
            ],
        )

    def test_the_cursor_reads_every_item_once_across_sources(self):
        day = self.days_ago(1)
        for parent in (self.pizza, self.emea, self.apac):
            for kind in ("activity", "calendar_event", "note", "survey", "ticket"):
                self.make(kind, parent, day=day)
            self.email(parent, at=timezone.now() - timedelta(minutes=5))
            self.call(parent, at=timezone.now() - timedelta(minutes=5))
            self.task(parent)
        self.snapshot(self.pizza, self.days_ago(70), "8.0")
        self.snapshot(self.pizza, self.days_ago(40), "3.0")
        everything = self.keys(self.story(limit="100"))
        self.assertEqual(len(everything), 25)
        self.assertEqual(len(set(everything)), 25)
        for limit in ("1", "2", "7"):
            with self.subTest(limit=limit):
                self.assertEqual(self.walk(limit=limit), everything)

    def test_a_cursor_cut_under_other_filters_reads_the_first_page(self):
        for _ in range(3):
            self.note(self.pizza)
        first = self.story(limit="1")
        self.assertIsNotNone(first["next_cursor"])
        moved = self.story(limit="1", group="tasks", cursor=first["next_cursor"])
        self.assertEqual(self.keys(moved), self.keys(first))

    def test_a_cursor_from_another_organisation_reads_the_first_page(self):
        taco = self.customer("Taco Co")
        for _ in range(3):
            self.note(self.pizza)
        for _ in range(3):
            self.note(taco)
        cursor = self.story(limit="1")["next_cursor"]
        self.assertIsNotNone(cursor)
        first = self.story(customer=taco, limit="1")
        moved = self.story(customer=taco, limit="1", cursor=cursor)
        self.assertEqual(self.keys(moved), self.keys(first))


class StoryHorizonTests(StoryFixture):
    def test_tomorrow_s_health_change_and_ticket_are_absent(self):
        """The future never happened yet: a health change dated tomorrow and a
        ticket opened tomorrow are absent from both `items` and
        `counts.by_kind`, through the full `build_story` response."""
        tomorrow = self.today + timedelta(days=1)
        self.snapshot(self.pizza, self.days_ago(30), "8.0")
        self.snapshot(self.pizza, tomorrow, "3.0")
        self.ticket(self.pizza, day=tomorrow)

        body = self.story()
        self.assertEqual(body["items"], [])
        self.assertEqual(body["counts"]["by_kind"]["health"], 0)
        self.assertEqual(body["counts"]["by_kind"]["ticket"], 0)


class StoryFilterTests(StoryFixture):
    def setUp(self):
        super().setUp()
        self.org_note = self.note(self.pizza, body="SSO is blocking the rollout")
        self.emea_email = self.email(self.emea)
        self.emea_ticket = self.ticket(self.emea)
        self.apac_survey = self.survey(self.apac)
        self.snapshot(self.apac, self.days_ago(70), "8.0")
        self.apac_fell = self.snapshot(self.apac, self.days_ago(40), "3.0")

    def found(self, **query):
        return set(self.keys(self.story(**query)))

    def test_group_narrows_to_its_kinds(self):
        self.assertEqual(self.found(group="conversations"), {("email", self.emea_email.pk)})
        self.assertEqual(self.found(group="tasks"), {("note", self.org_note.pk)})
        self.assertEqual(self.found(group="feedback"), {("survey", self.apac_survey.pk)})
        self.assertEqual(self.found(group="health"), {("health", self.apac_fell.pk)})

    def test_source_picks_exact_kinds(self):
        self.assertEqual(
            self.found(source="ticket,survey"),
            {("ticket", self.emea_ticket.pk), ("survey", self.apac_survey.pk)},
        )

    def test_account_narrows_to_one_account_or_the_organisation(self):
        self.assertEqual(
            self.found(account=str(self.emea.pk)),
            {("email", self.emea_email.pk), ("ticket", self.emea_ticket.pk)},
        )
        self.assertEqual(self.found(account="none"), {("note", self.org_note.pk)})
        self.assertEqual(
            self.found(account=str(self.apac.pk), group="health"),
            {("health", self.apac_fell.pk)},
        )

    def test_an_account_of_another_organisation_reads_nothing(self):
        stranger = self.account("Taco div", customers=[self.customer("Taco Co")])
        self.note(stranger)
        body = self.story(account=str(stranger.pk))
        self.assertEqual(body["items"], [])
        self.assertEqual(body["counts"]["by_group"]["all"], 0)

    def test_search_matches_text_and_leaves_health_out(self):
        self.assertEqual(self.found(q="sso"), {("note", self.org_note.pk)})
        self.assertEqual(self.found(q="poor"), set())
        self.assertEqual(self.story(q="sso")["counts"]["by_group"]["health"], 0)

    def test_thread_reads_one_email_thread(self):
        first = self.email(self.pizza, thread_id="t-1")
        reply = self.email(self.emea, thread_id="t-1")
        self.email(self.pizza, thread_id="t-2")
        self.assertEqual(self.found(thread="t-1"), {("email", first.pk), ("email", reply.pk)})

    def test_counts_cover_the_whole_filtered_set_not_the_page(self):
        body = self.story(limit="1")
        self.assertEqual(len(body["items"]), 1)
        self.assertEqual(
            body["counts"]["by_group"],
            {"all": 5, "conversations": 1, "tickets": 1, "tasks": 1, "feedback": 1, "health": 1},
        )
        self.assertEqual(
            body["counts"]["by_kind"],
            {
                "activity": 0,
                "calendar_event": 0,
                "call": 0,
                "email": 1,
                "health": 1,
                "note": 1,
                "survey": 1,
                "task": 0,
                "ticket": 1,
            },
        )
        self.assertEqual(
            body["counts"]["by_account"],
            {"all": 5, "none": 1, str(self.apac.pk): 2, str(self.emea.pk): 2},
        )

    def test_by_group_ignores_the_group_and_by_account_ignores_the_account(self):
        body = self.story(group="conversations", account=str(self.emea.pk))
        self.assertEqual(body["counts"]["by_group"]["all"], 2)
        self.assertEqual(body["counts"]["by_group"]["tickets"], 1)
        self.assertEqual(
            body["counts"]["by_account"],
            {"all": 1, "none": 0, str(self.apac.pk): 0, str(self.emea.pk): 1},
        )


class StoryTieTests(StoryFixture):
    def test_the_cursor_walks_ties_across_every_kind_including_health(self):
        tied = self.days_ago(40)
        self.snapshot(self.pizza, self.days_ago(70), "8.0")
        self.snapshot(self.pizza, tied, "3.0")
        self.snapshot(self.emea, self.days_ago(70), "8.0")
        self.snapshot(self.emea, tied, "3.0")
        for parent in (self.pizza, self.emea):
            for kind in ("activity", "calendar_event", "note", "survey", "ticket"):
                self.make(kind, parent, day=tied)
                self.make(kind, parent, day=tied)
        everything = self.keys(self.story(limit="100"))
        self.assertEqual(len(everything), 22)
        self.assertEqual(len(set(everything)), 22)
        self.assertEqual(
            [kind for kind, _ in everything],
            sorted((kind for kind, _ in everything), reverse=True),
        )
        for limit in ("1", "3", "4", "5"):
            with self.subTest(limit=limit):
                self.assertEqual(self.walk(limit=limit), everything)


class StoryCountsMatchItemsTests(StoryFixture):
    def setUp(self):
        super().setUp()
        for parent in (self.pizza, self.emea, self.apac):
            for kind in ("activity", "calendar_event", "note", "survey", "ticket", "task"):
                self.make(kind, parent)
        self.email(self.emea, subject="SSO rollout")
        self.call(self.apac, title="SSO review")
        self.snapshot(self.apac, self.days_ago(70), "8.0")
        self.snapshot(self.apac, self.days_ago(40), "3.0")

    def check(self, **query):
        counts = self.story(**query)["counts"]
        for kind, n in counts["by_kind"].items():
            with self.subTest(kind=kind, query=query):
                self.assertEqual(len(self.walk(limit="7", source=kind, **query)), n)
        for group, n in counts["by_group"].items():
            with self.subTest(group=group, query=query):
                narrowed = {} if group == "all" else {"group": group}
                self.assertEqual(len(self.walk(limit="7", **narrowed, **query)), n)
        rest = {k: v for k, v in query.items() if k != "account"}
        for account, n in counts["by_account"].items():
            with self.subTest(by_account=account, query=query):
                narrowed = {} if account == "all" else {"account": account}
                self.assertEqual(len(self.walk(limit="7", **narrowed, **rest)), n)

    def test_every_count_is_the_number_of_items_its_filter_reads(self):
        self.check()
        self.check(q="sso")
        self.check(account=str(self.emea.pk))
        self.check(account="none", q="sso")

    def test_by_account_follows_the_group_and_source(self):
        by_account = self.story(group="tickets")["counts"]["by_account"]
        self.assertEqual(
            by_account, {"all": 3, "none": 1, str(self.apac.pk): 1, str(self.emea.pk): 1}
        )
        by_account = self.story(source="email,call")["counts"]["by_account"]
        self.assertEqual(
            by_account, {"all": 2, "none": 0, str(self.apac.pk): 1, str(self.emea.pk): 1}
        )


class StoryVisibilityTests(StoryFixture):
    def setUp(self):
        super().setUp()
        self.shown = [
            ("note", self.note(self.pizza, author=self.csm).pk),
            ("ticket", self.ticket(self.emea, department="cs").pk),
            ("email", self.email(self.apac, mailbox_owner=self.csm).pk),
        ]
        self.engineering = self.ticket(self.emea, department="engineering")
        self.hidden = [
            ("note", self.note(self.pizza, author=self.other).pk),
            ("ticket", self.engineering.pk),
            ("email", self.email(self.apac, mailbox_owner=self.other).pk),
            ("task", self.task(self.pizza, created_by=self.other, assignee=self.other).pk),
        ]

    def test_items_and_counts_leave_out_what_the_viewer_may_not_read(self):
        body = self.story()
        self.assertEqual(set(self.keys(body)), set(self.shown))
        counts = body["counts"]
        self.assertEqual(counts["by_group"]["all"], 3)
        self.assertEqual(counts["by_group"]["tasks"], 1)
        self.assertEqual(counts["by_kind"]["task"], 0)
        self.assertEqual(counts["by_kind"]["ticket"], 1)
        self.assertEqual(counts["by_kind"]["email"], 1)
        self.assertEqual(
            counts["by_account"],
            {"all": 3, "none": 1, str(self.apac.pk): 1, str(self.emea.pk): 1},
        )

    def test_a_search_counts_only_what_the_viewer_may_read(self):
        counts = self.story(q="renewal")["counts"]
        self.assertEqual(counts["by_kind"]["email"], 1)
        self.assertEqual(counts["by_group"]["all"], 1)

    def test_a_wider_rule_reads_and_counts_more(self):
        body = self.story(user=self.admin)
        self.assertIn(("ticket", self.engineering.pk), self.keys(body))
        self.assertEqual(body["counts"]["by_kind"]["ticket"], 2)
        self.assertEqual(body["counts"]["by_group"]["all"], len(body["items"]))

    def test_an_account_the_viewer_may_not_open_is_neither_read_nor_counted(self):
        open_co = self.customer("Open Co", owner=None)
        free = self.account("Free div", customers=[open_co])
        danas = self.account("Dana's div", customers=[open_co], owner=self.other)
        seen = self.note(free)
        self.note(danas)
        self.activity(danas)
        self.snapshot(danas, self.days_ago(70), "8.0")
        self.snapshot(danas, self.days_ago(40), "3.0")
        body = self.story(customer=open_co)
        self.assertEqual(self.keys(body), [("note", seen.pk)])
        self.assertEqual(body["counts"]["by_group"]["all"], 1)
        self.assertEqual(body["counts"]["by_kind"]["health"], 0)
        self.assertEqual(body["counts"]["by_account"], {"all": 1, "none": 0, str(free.pk): 1})
        named = self.story(customer=open_co, account=str(danas.pk))
        self.assertEqual(named["items"], [])
        self.assertEqual(named["counts"]["by_group"]["all"], 0)


class StoryQueryCountTests(StoryFixture):
    def fill(self, start, stop):
        for i in range(start, stop):
            for parent in (self.pizza, self.emea, self.apac):
                for kind in ("activity", "calendar_event", "note", "survey", "ticket"):
                    self.make(kind, parent, day=self.days_ago(i))
                self.task(parent)
                self.email(parent)
                self.call(parent)
                self.snapshot(parent, self.days_ago(40 + 30 * i), str(3 + 5 * (i % 2)))
        # Content for every entry of `attention`, not just rows for the stream:
        # an open High ticket, an open question and a live anomaly with call
        # evidence the viewer may read. `urgent_tickets`/`overdue_tasks`/
        # `open_questions`/`latest_anomaly` run the same fixed aggregate and
        # exists queries whether or not a row matches, so this must not change
        # the pinned count below — it only proves the count was never counting
        # rows in the first place.
        self.ticket(self.pizza, priority=Ticket.Priority.HIGH, day=self.days_ago(start))
        Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.csm,
            assignee=self.engineer,
            text=f"Why {start}?",
        )
        now = timezone.now()
        anomaly = Anomaly.objects.create(
            organisation=self.org, title=f"Fault {start}", first_seen_at=now, last_seen_at=now
        )
        AnomalyEvidence.objects.create(
            anomaly=anomaly,
            organisation=self.org,
            kind=AnomalyEvidence.Kind.CALL,
            record_id=start,
            customer=self.pizza,
            snippet="fault",
            occurred_at=now,
        )

    def queries(self, **query):
        with CaptureQueriesContext(connection) as ctx:
            body = self.story(**query)
        return len(ctx), body

    def test_a_page_costs_the_same_queries_at_two_book_sizes(self):
        self.fill(0, 2)
        self.story()  # the viewer's membership is read once and then cached on them
        small, _ = self.queries(limit="5")
        self.fill(2, 8)
        large, first = self.queries(limit="5")
        self.assertIsNotNone(first["next_cursor"])
        after, _ = self.queries(limit="5", cursor=first["next_cursor"])
        searched, _ = self.queries(limit="5", q="sso", account=str(self.emea.pk))
        self.assertEqual(small, large)
        self.assertEqual(large, after)
        # The scope (3), the org chart for mail, notes and tasks (3), health (1),
        # one page query and one count per record source (8 + 8), then the
        # attention block's own aggregates and rules (8): tickets, tasks,
        # the questions rule's org chart plus its count, and the anomaly
        # rule's org chart plus its evidence query. Both book sizes carry a
        # real open High ticket, open question and live anomaly (see `fill`),
        # so this is the cost of an attention block with content, not an
        # empty one; it stays 31 because those rules are aggregates/`.first()`
        # calls, never one query per matching row.
        self.assertEqual(large, 31)
        self.assertLessEqual(searched, large)
