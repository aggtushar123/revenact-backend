"""What the model is told on one account's page: the account's row, what
needs attention, the story's counts and its last 30 days, the item asked
about and the records behind the question — each read under its own rule.
And what a shared reader will be checked against."""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.db import connection
from django.http import Http404
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from services.account_story.tests.fixtures import AccountStoryFixture
from services.accounts.models import User
from services.copilot.account_detail_grounding import (
    build_account_detail_grounding,
    renewal_text,
)
from services.copilot.grounded_records import account_ref, record_ref
from services.copilot.organization_detail_grounding import STORY_ITEMS
from services.customers.models import Account, Ticket


def _in_order(query, candidates):
    return [(index, 1.0) for index in range(len(candidates))]


class AccountDetailFixture(AccountStoryFixture):
    """Carl owns Pizza Hut; its EMEA and APAC accounts are unowned. Dana is
    another CSM, Alice the Leadership admin, Erin an engineer."""

    def setUp(self):
        super().setUp()
        patcher = patch("services.copilot.retrieval.rank_by_similarity", side_effect=_in_order)
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def detail(account, focus=None):
        return {
            "surface": "accounts",
            "view": "detail",
            "account": account.pk,
            "label": account.name,
            "focus": focus,
        }

    def ground(self, user=None, account=None, focus=None, question=""):
        return build_account_detail_grounding(
            user or self.csm, self.detail(account or self.emea, focus), question, today=self.today
        )


class AccountDigestTests(AccountDetailFixture):
    def test_the_digest_opens_with_the_page_and_the_accounts_row_without_the_ai_pulse_reason(
        self,
    ):
        Account.objects.filter(pk=self.emea.pk).update(
            health_score=Decimal("2.0"),
            arr=Decimal("50000"),
            renewal_date=self.today - timedelta(days=5),
            nps_score=-20,
            csat_score=Decimal("3.5"),
            lifecycle_stage="live",
            owner=self.csm,
            ai_pulse_reason="The champion sounded unhappy on the call",
        )

        summary = self.ground().summary

        lines = summary.split("\n")
        self.assertEqual(lines[0], "Screen: Accounts › EMEA (one account's page)")
        self.assertEqual(lines[1], "Part of: Pizza Hut")
        self.assertEqual(lines[2], "Currency: USD")
        self.assertIn("Account: EMEA; lifecycle Live; owner Carl CSM", summary)
        self.assertIn("  Health poor (2.0/10); trend over the last months", summary)
        self.assertIn(
            f"  ARR 50,000.00 USD; renewal was due {self.today - timedelta(days=5)} "
            "(5 days overdue); NPS -20; CSAT 3.5",
            summary,
        )
        self.assertIn("  Triage risk ", summary)
        self.assertIn("  Signal: Renewal overdue", summary)
        # Model-written from records under nobody's rule: never quoted.
        self.assertNotIn("champion sounded unhappy", summary)

    def test_unset_scores_read_not_set(self):
        self.assertIn("NPS not set; CSAT not set", self.ground().summary)

    def test_part_of_and_the_snapshot_name_only_organisations_the_asker_can_open(self):
        taco = self.customer("Taco Bell", owner=self.other)
        self.emea.customers.add(taco)

        grounding = self.ground()

        self.assertIn("Part of: Pizza Hut\n", grounding.summary)
        self.assertNotIn("Taco Bell", grounding.summary)
        self.assertEqual(grounding.customer_ids, [self.pizza.pk])
        self.assertIn("Part of: Pizza Hut, Taco Bell\n", self.ground(self.admin).summary)

    def test_needs_attention_states_the_renewal_and_urgent_tickets_but_not_overdue_tasks(self):
        Account.objects.filter(pk=self.emea.pk).update(renewal_date=self.today + timedelta(days=12))
        self.ticket(self.emea, priority=Ticket.Priority.HIGH, day=self.days_ago(3))
        self.task(self.emea, due=self.days_ago(2))
        # The story itself does flag the overdue task for Carl...
        self.assertIsNotNone(self.account_story()["attention"]["overdue_tasks"])

        summary = self.ground().summary

        self.assertIn("Needs attention:", summary)
        self.assertIn(f"Renewal due {self.today + timedelta(days=12)} (in 12 days)", summary)
        self.assertIn("1 open High or Critical ticket, oldest opened 3 days ago", summary)
        # ...but overdue tasks follow the asker's own task chain; the digest
        # may be shown to shared readers, so it never states them.
        self.assertNotIn("overdue task", summary.lower())

    def test_nothing_needs_attention_says_so(self):
        self.assertIn("Needs attention: nothing.", self.ground().summary)

    def test_the_story_counts_and_its_last_thirty_days_are_this_accounts_only(self):
        self.email(self.emea, subject="Renewal terms", at=timezone.now() - timedelta(days=2))
        self.note(self.emea, title="Champion left", day=self.days_ago(29))
        self.note(self.emea, title="Kickoff notes", day=self.days_ago(45))
        self.call(self.emea, title="QBR", at=timezone.now() - timedelta(days=1))
        self.note(self.pizza, title="Organisation note")
        self.note(self.apac, title="APAC note")

        summary = self.ground().summary

        self.assertIn(
            "Story records counted up to today (emails, notes and tasks are not counted): "
            "1 — Conversations 1; Tickets 0; Feedback 0; Health & usage 0",
            summary,
        )
        self.assertIn(
            "· Email · EMEA · Renewal terms: Can we talk about the renewal? (Pat Buyer)", summary
        )
        self.assertIn(
            f"{self.days_ago(29)} · Note · EMEA · Champion left: Sam moved on. (Carl CSM)",
            summary,
        )
        # Older than the window: out of the story (retrieval may still quote it).
        self.assertNotIn("· Note · EMEA · Kickoff notes", summary)
        self.assertNotIn("Organisation note", summary)
        self.assertNotIn("APAC note", summary)

    def test_the_story_is_capped(self):
        for n in range(STORY_ITEMS + 5):
            self.note(self.emea, title=f"Note {n:02d}", day=self.days_ago(n % 20))

        grounding = self.ground()

        self.assertEqual(grounding.summary.count(" · Note · "), STORY_ITEMS)
        self.assertEqual(
            len([ref for ref in grounding.records if ref["type"] == "note"]), STORY_ITEMS
        )

    def test_nothing_recent_says_so(self):
        self.assertIn("Recent story (last 30 days): nothing.", self.ground().summary)

    def test_records_the_asker_may_not_read_never_reach_the_digest(self):
        self.note(self.emea, title="Dana's private note", author=self.other)
        self.task(self.emea, title="Dana's private task", created_by=self.other)
        self.email(self.emea, subject="Dana's mailbox", mailbox_owner=self.other)
        self.ticket(self.emea, title="Engineering-only outage", department="engineering")
        # The same records under Carl's own rule are quoted, so only the rule
        # keeps Dana's out.
        self.note(self.emea, title="Carl's own note", author=self.csm)
        self.task(self.emea, title="Carl's own task", created_by=self.csm)
        self.email(self.emea, subject="Carl's mailbox", mailbox_owner=self.csm)
        self.ticket(self.emea, number="T-2", title="CS outage", department="cs")

        summary = self.ground().summary

        for text in ("Carl's own note", "Carl's own task", "Carl's mailbox", "CS outage"):
            with self.subTest(readable=text):
                self.assertIn(text, summary)
        for text in (
            "Dana's private note",
            "Dana's private task",
            "Dana's mailbox",
            "Engineering-only outage",
        ):
            with self.subTest(hidden=text):
                self.assertNotIn(text, summary)
        self.assertIn("Engineering-only outage", self.ground(self.admin).summary)

    def test_the_focus_item_is_quoted_even_when_older_than_the_window(self):
        old = self.note(self.emea, title="Old champion note", day=self.days_ago(90))

        grounding = self.ground(focus={"kind": "note", "id": old.pk})

        self.assertIn("The asker is asking about this story item:", grounding.summary)
        self.assertIn("Old champion note", grounding.summary)
        self.assertIn(
            record_ref("note", old.pk, customer_id=None, account_id=self.emea.pk),
            grounding.records,
        )

    def test_a_focus_the_asker_can_no_longer_read_is_not_quoted(self):
        private = self.note(self.emea, title="Dana's private note", author=self.other)

        grounding = self.ground(focus={"kind": "note", "id": private.pk})

        self.assertIn(
            "The story item asked about is not one the asker can read here.", grounding.summary
        )
        self.assertNotIn("Dana's private note", grounding.summary)
        self.assertNotIn(
            record_ref("note", private.pk, customer_id=None, account_id=self.emea.pk),
            grounding.records,
        )

    def test_records_are_retrieved_for_the_question_on_the_account_only(self):
        on_emea = self.email(
            self.emea, subject="EMEA renewal", at=timezone.now() - timedelta(days=60)
        )
        self.note(self.pizza, title="Organisation renewal")

        grounding = self.ground(question="What blocks the renewal?")

        self.assertIn("Records for EMEA:", grounding.summary)
        self.assertEqual(
            [(s["type"], s["id"], s["company_type"]) for s in grounding.sources],
            [("email", on_emea.pk, "account")],
        )
        self.assertEqual(grounding.company, self.emea)

    def test_an_account_the_asker_cannot_open_is_a_404(self):
        carls = self.account("Carl's own", owner=self.csm)
        globex = self.customer("Globex Corp", organisation=self.other_org)
        elsewhere = self.account("Globex EU", customers=[globex])
        for user, account in ((self.other, carls), (self.csm, elsewhere)):
            with self.subTest(account=account.name), self.assertRaises(Http404):
                self.ground(user, account)


class RenewalTextTests(AccountDetailFixture):
    def renewal(self, days):
        due = None if days is None else self.today + timedelta(days=days)
        Account.objects.filter(pk=self.emea.pk).update(renewal_date=due)
        return due, self.ground().summary

    def test_each_renewal_reads_as_the_row_reads_it(self):
        cases = {
            None: "no renewal date",
            0: "renews today ({due})",
            1: "renews {due} (in 1 day)",
            40: "renews {due} (in 40 days)",
            -1: "renewal was due {due} (1 day overdue)",
        }
        for days, text in cases.items():
            with self.subTest(days=days):
                due, summary = self.renewal(days)
                self.assertIn(f"; {text.format(due=due)}; NPS", summary)

    def test_renewal_text_reads_the_accounts_renewal(self):
        due = self.today + timedelta(days=3)

        class Entry:
            account = Account(renewal_date=due)
            renewal_days = 3

        self.assertEqual(renewal_text(Entry), f"renews {due} (in 3 days)")


class AccountSnapshotTests(AccountDetailFixture):
    def test_the_snapshot_is_the_organisations_named_its_tickets_and_every_record(self):
        note = self.note(self.emea)
        ticket = self.ticket(self.emea, priority=Ticket.Priority.HIGH, department="cs")

        grounding = self.ground()

        self.assertEqual(grounding.customer_ids, [self.pizza.pk])
        self.assertEqual(grounding.pipeline, {"account_ids": [], "departments": []})
        self.assertEqual(grounding.tickets, {"account_ids": [self.emea.pk], "departments": ["cs"]})
        self.assertEqual(
            grounding.records,
            [
                account_ref(self.emea.pk),
                record_ref("note", note.pk, customer_id=None, account_id=self.emea.pk),
                record_ref("ticket", ticket.pk, customer_id=None, account_id=self.emea.pk),
            ],
        )

    def test_an_account_with_no_organisation_the_asker_can_open_names_none(self):
        taco = self.customer("Taco Bell", owner=self.other)
        lone = self.account("Lone", customers=[taco])

        grounding = self.ground(account=lone)

        self.assertIn("Part of: no organisation the asker can open", grounding.summary)
        self.assertEqual(grounding.customer_ids, [])


class AccountQueryCountTests(AccountDetailFixture):
    #: Measured with CaptureQueriesContext on this fixture: 34, the same with
    #: one record of each kind as with many. 3 for the asker's org chart, 1
    #: for the account, 4 for its row, 9 story reads, 8 counts, 2 attention
    #: aggregates, 5 for retrieval and 2 for the ticket snapshot. The
    #: organisation page reads 42: it also names its accounts, converts ARR
    #: and reads Knowledge questions and anomalies, which an account has not.
    #: A change is a regression to explain, not absorb.
    PINNED_QUERIES = 34

    def queries(self, **kwargs):
        # A fresh asker each time, as each request has: the org chart is
        # memoised on the user instance.
        user = User.objects.select_related("organisation").get(pk=self.csm.pk)
        with CaptureQueriesContext(connection) as captured:
            self.ground(user, **kwargs)
        return len(captured)

    def fill(self, parent, n, *, start=0):
        # `start` keeps each snapshot on its own day: one per account per date.
        for i in range(start, start + n):
            self.email(parent, subject=f"Mail {i}", at=timezone.now() - timedelta(hours=i + 1))
            self.note(parent, title=f"Note {i}", day=self.days_ago(i))
            self.task(parent, title=f"Task {i}", due=self.days_ago(i))
            self.ticket(parent, number=f"T-{parent.pk}-{i}", priority=Ticket.Priority.HIGH)
            self.call(parent, title=f"Call {i}", at=timezone.now() - timedelta(hours=i + 2))
            self.snapshot(parent, self.days_ago(60 + i), Decimal(8 - i % 3))

    def test_the_grounding_reads_a_constant_number_of_queries(self):
        self.fill(self.emea, 1)
        small = self.queries(question="How is it going?")
        self.fill(self.emea, 6, start=1)
        self.fill(self.apac, 4)
        self.emea.customers.add(self.customer("Hooli"))
        large = self.queries(question="How is it going?")

        self.assertEqual(small, large)
        self.assertEqual(large, self.PINNED_QUERIES)
