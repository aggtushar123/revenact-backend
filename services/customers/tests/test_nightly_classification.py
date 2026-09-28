"""`classify_interactions` as the nightly job: one tenant at a time, metered
to that tenant, capped, stopping cleanly at a tenant's budget, never guessing
a call, and moving the contacts on what it reads."""

from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from services.accounts.models import Organisation
from services.copilot.anthropic_client import BudgetExceeded
from services.customers.models import Call, Contact, Customer, Email

BATCH = "services.customers.management.commands.classify_interactions.classify_batch"


def answer(batch, sentiment="negative"):
    return {
        f"{r._meta.model_name}:{r.pk}": {
            "ai_area": "product_growth",
            "ai_category": "bug_report",
            "ai_subcategory": "ui_bug",
            "sentiment": sentiment,
        }
        for r in batch
    }


class NightlyCommandTests(TestCase):
    def setUp(self):
        self.acme = Organisation.objects.create(name="Acme")
        self.globex = Organisation.objects.create(name="Globex")
        self.pizza = Customer.objects.create(organisation=self.acme, name="Pizza Hut")
        self.initech = Customer.objects.create(organisation=self.globex, name="Initech")

    def email(self, customer, n=0):
        return Email.objects.create(
            customer=customer,
            subject=f"Subject {n}",
            sender_name="Ada",
            recipient_name="Support",
            body="Body.",
            sent_at=timezone.now(),
        )

    def call(self, title="Renewal readiness", summary=""):
        return Call.objects.create(
            customer=self.pizza,
            title=title,
            summary=summary,
            host_name="Carl",
            occurred_at=timezone.now(),
        )

    def run_command(self, **kwargs):
        out, err = StringIO(), StringIO()
        call_command("classify_interactions", stdout=out, stderr=err, **kwargs)
        return out.getvalue()

    def test_each_batch_is_one_tenants_and_metered_to_it(self):
        mine = [self.email(self.pizza, n) for n in range(2)]
        theirs = [self.email(self.initech, n) for n in range(2)]
        with patch(BATCH, side_effect=lambda batch, **_: answer(batch)) as batch:
            self.run_command()
        seen = {
            call.kwargs["organisation"].pk: {r.pk for r in call.args[0]}
            for call in batch.call_args_list
        }
        self.assertEqual(
            seen,
            {self.acme.pk: {e.pk for e in mine}, self.globex.pk: {e.pk for e in theirs}},
        )

    def test_a_capped_run_takes_the_tenants_in_turn(self):
        for n in range(3):
            self.email(self.pizza, n)
        self.email(self.initech)
        with patch(BATCH, side_effect=lambda batch, **_: answer(batch)):
            self.run_command(limit=2)
        classified = Email.objects.exclude(ai_classified_at=None)
        self.assertEqual(sorted(e.customer.name for e in classified), ["Initech", "Pizza Hut"])

    def test_a_tenant_out_of_budget_stops_cleanly_and_the_rest_carry_on(self):
        self.email(self.pizza)
        other = self.email(self.initech)

        def budget(batch, organisation, **_):
            if organisation == self.acme:
                raise BudgetExceeded("Acme is out of credits")
            return answer(batch)

        with patch(BATCH, side_effect=budget):
            out = self.run_command()
        self.assertIn("Stopped at the AI budget for: Acme.", out)
        other.refresh_from_db()
        self.assertIsNotNone(other.ai_classified_at)
        self.assertEqual(Email.objects.filter(ai_classified_at=None).count(), 1)

    def test_a_call_with_nothing_to_read_is_marked_without_a_model_call(self):
        call = self.call(title="Weekly sync")
        with patch(BATCH) as batch:
            out = self.run_command()
        batch.assert_not_called()
        call.refresh_from_db()
        self.assertEqual(call.analysis, "not_analysable")
        self.assertIn("1 call(s) not analysable", out)

    def test_a_call_the_model_declines_is_not_analysable_and_not_retried(self):
        call = self.call(summary="Hmm.")
        with patch(BATCH, return_value={}):
            self.run_command()
        call.refresh_from_db()
        self.assertEqual(call.analysis, "not_analysable")
        with patch(BATCH) as batch:
            self.assertIn("Nothing to classify", self.run_command())
        batch.assert_not_called()

    def test_classifying_moves_the_people_on_the_call(self):
        sam = Contact.objects.create(customer=self.pizza, name="Sam", email="sam@pizza.io")
        call = self.call(summary="They are unhappy.")
        call.participants.set([sam])
        with patch(BATCH, side_effect=lambda batch, **_: answer(batch)):
            self.run_command()
        sam.refresh_from_db()
        self.assertEqual((sam.sentiment, sam.sentiment_source), ("negative", "computed"))

    def test_the_dry_run_counts_one_model_call_per_tenant(self):
        self.email(self.pizza)
        self.email(self.initech)
        with patch(BATCH) as batch:
            out = self.run_command(dry_run=True)
        batch.assert_not_called()
        self.assertIn("Would classify 2 record(s)", out)
        self.assertIn("in 2 model call(s)", out)

    def test_a_call_declined_on_reclassify_clears_stale_sentiment_and_recomputes(self):
        """A call already carrying a sentiment from an earlier pass, declined
        on a --reclassify run, must not keep reading as analysed with that
        stale sentiment — and the contacts on it move with it."""
        sam = Contact.objects.create(customer=self.pizza, name="Sam", email="sam@pizza.io")
        call = self.call(summary="They are unhappy.")
        call.participants.set([sam])
        with patch(BATCH, side_effect=lambda batch, **_: answer(batch)):
            self.run_command()
        call.refresh_from_db()
        sam.refresh_from_db()
        self.assertEqual(call.sentiment, "negative")
        self.assertEqual((sam.sentiment, sam.sentiment_source), ("negative", "computed"))

        with patch(BATCH, return_value={}):
            self.run_command(reclassify=True)

        call.refresh_from_db()
        sam.refresh_from_db()
        self.assertEqual(call.analysis, "not_analysable")
        self.assertEqual(call.sentiment, "neutral")
        # The evidence is gone, so the contact falls back to hand-set.
        self.assertEqual(sam.sentiment_source, "manual")

    # Final fix wave (D1, D2, D3, D4, D7).

    def test_an_organisation_that_is_not_active_is_skipped(self):
        """A suspended, archived or not-yet-active tenant is not spent on."""
        for state in ("suspended", "archived", "pending"):
            with self.subTest(state=state):
                Email.objects.all().delete()
                self.globex.status = state
                self.globex.save(update_fields=["status"])
                self.email(self.pizza)
                self.email(self.initech)
                with patch(BATCH, side_effect=lambda batch, **_: answer(batch)) as batch:
                    self.run_command()
                self.assertEqual(
                    {call.kwargs["organisation"].pk for call in batch.call_args_list},
                    {self.acme.pk},
                )

    def test_an_empty_placement_for_a_lone_call_is_a_decline(self):
        """The same predicate as classify_records: a placement with no
        fields is no placement, so a lone call is declined, not analysed."""
        call = self.call(summary="Hmm.")
        with patch(BATCH, return_value={f"call:{call.pk}": {}}):
            self.run_command()
        call.refresh_from_db()
        self.assertEqual(call.analysis, "not_analysable")

    def test_both_callers_decide_through_one_predicate(self):
        from services.customers import classification
        from services.customers.classification import classify_records

        first, second = self.call(summary="One."), self.call(summary="Two.")
        spy = patch("services.customers.classification.reading_of", wraps=classification.reading_of)
        with spy as reading, patch(BATCH, return_value={}):
            self.run_command()
        self.assertEqual(reading.call_count, 2)
        with (
            spy as reading,
            patch("services.customers.classification.classify_batch", return_value={}),
        ):
            classify_records([Call.objects.get(pk=first.pk)])
        self.assertEqual(reading.call_count, 1)
        self.assertEqual(Call.objects.get(pk=second.pk).analysis, "pending")

    def test_a_capped_run_takes_a_tenants_oldest_records_across_kinds(self):
        from datetime import timedelta

        from services.customers.models import Ticket

        now = timezone.now()
        oldest = self.email(self.pizza)
        Email.objects.filter(pk=oldest.pk).update(sent_at=now - timedelta(days=3))
        middle = Ticket.objects.create(
            customer=self.pizza,
            ticket_number="T-1",
            title="Export fails",
            priority="high",
            opened_at=(now - timedelta(days=2)).date(),
        )
        newest = self.call(summary="Unhappy.")
        Call.objects.filter(pk=newest.pk).update(occurred_at=now - timedelta(days=1))
        with patch(BATCH, side_effect=lambda batch, **_: answer(batch)) as batch:
            self.run_command(limit=2)
        sent = [r._meta.model_name for r in batch.call_args.args[0]]
        self.assertEqual(sent, ["email", "ticket"])
        newest.refresh_from_db()
        self.assertEqual(newest.analysis, "pending")
        self.assertEqual(middle.__class__.objects.get(pk=middle.pk).analysis, "analysed")

    def test_the_pre_pass_recomputes_contacts_once(self):
        from services.customers import contact_sentiment

        sam = Contact.objects.create(customer=self.pizza, name="Sam", email="sam@pizza.io")
        for _ in range(3):
            self.call(title="Weekly sync").participants.set([sam])
        with (
            patch(BATCH) as batch,
            patch.object(
                contact_sentiment,
                "recompute_for_records",
                wraps=contact_sentiment.recompute_for_records,
            ) as recompute,
        ):
            self.run_command()
        batch.assert_not_called()
        self.assertEqual(recompute.call_count, 1)
        self.assertEqual(len(recompute.call_args.args[0]), 3)

    def test_the_dry_run_leaves_out_calls_marked_for_free(self):
        self.call(title="Weekly sync")
        self.email(self.pizza)
        with patch(BATCH) as batch:
            out = self.run_command(dry_run=True)
        batch.assert_not_called()
        self.assertIn("Would classify 1 record(s) — 1 email(s) — in 1 model call(s)", out)
        self.assertIn("1 call(s) would be marked not analysable", out)
        self.assertEqual(Call.objects.get().analysis, "pending")

    def test_an_empty_reply_across_several_calls_leaves_them_pending(self):
        calls = [self.call(summary="One."), self.call(summary="Two.")]
        with patch(BATCH, return_value={}):
            self.run_command()
        for call in calls:
            call.refresh_from_db()
            self.assertEqual(call.analysis, "pending")

    def test_a_budget_stop_part_way_through_a_tenants_batches(self):
        for n in range(3):
            self.email(self.pizza, n)
        replies = iter([answer, BudgetExceeded("spent")])

        def budget(batch, **_):
            reply = next(replies)
            if isinstance(reply, Exception):
                raise reply
            return reply(batch)

        with (
            patch("services.customers.management.commands.classify_interactions.BATCH_SIZE", 2),
            patch(BATCH, side_effect=budget),
        ):
            out = self.run_command()
        self.assertIn("classified 2 of 3", out)
        self.assertIn("Stopped at the AI budget for: Acme.", out)
        self.assertEqual(Email.objects.filter(ai_classified_at=None).count(), 1)

    def test_an_account_level_record_is_no_evidence_for_another_tenant(self):
        from datetime import date

        from services.customers.models import Account, Ticket

        uk = Account.objects.create(name="Pizza Hut UK")
        uk.customers.add(self.pizza)
        uma = Contact.objects.create(account=uk, name="Uma", email="uma@hut.co.uk")
        stranger = Contact.objects.create(
            customer=self.initech, name="Uma elsewhere", email="uma@hut.co.uk"
        )
        Email.objects.create(
            account=uk,
            subject="Broken",
            sender_name="Uma",
            recipient_name="Support",
            body="It is broken.",
            sent_at=timezone.now(),
            from_address="uma@hut.co.uk",
        )
        Ticket.objects.create(
            account=uk,
            ticket_number="T-9",
            title="Broken",
            priority="high",
            opened_at=date.today(),
            requester_email="uma@hut.co.uk",
        )
        with patch(BATCH, side_effect=lambda batch, **_: answer(batch)):
            self.run_command()
        uma.refresh_from_db()
        stranger.refresh_from_db()
        self.assertEqual((uma.sentiment_source, uma.sentiment_evidence["emails"]), ("computed", 1))
        self.assertEqual(uma.sentiment_evidence["tickets"], 1)
        self.assertEqual((stranger.sentiment_source, stranger.sentiment_evidence), ("manual", {}))

    def test_a_record_deleted_mid_run_is_skipped(self):
        """Deleted between reading the pending pairs and fetching the rows:
        skipped, not a KeyError."""
        from django.db.models.query import QuerySet

        gone, kept = self.email(self.pizza, 1), self.email(self.pizza, 2)
        original = QuerySet.in_bulk

        def delete_first(queryset, *args, **kwargs):
            Email.objects.filter(pk=gone.pk).delete()
            return original(queryset, *args, **kwargs)

        with (
            patch.object(QuerySet, "in_bulk", delete_first),
            patch(BATCH, side_effect=lambda batch, **_: answer(batch)) as batch,
        ):
            out = self.run_command()
        self.assertEqual([r.pk for r in batch.call_args.args[0]], [kept.pk])
        self.assertIn("classified 1 of 1", out)
