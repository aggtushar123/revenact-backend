"""The classifier: how a model answer becomes a taxonomy, and the two commands
that write one.

Nothing here makes a real call — `get_completion` is patched throughout, the same
way every Copilot and Headline test in this repo does it. The interesting
behaviour is all in what happens to an *imperfect* answer, because a closed
vocabulary and a generative model disagree regularly.
"""

from io import StringIO
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from services.accounts.models import Organisation, User
from services.copilot.anthropic_client import CopilotNotConfigured, CopilotRequestFailed
from services.customers import classification, taxonomy
from services.customers.classification import (
    BATCH_SIZE,
    MAX_TEXT_CHARS,
    _options_block,
    apply_classification,
    build_prompt,
    classify_batch,
)
from services.customers.management.commands.seed_demo_classifications import (
    classify_text,
    sentiment_for,
)
from services.customers.models import Call, Customer, Email, Ticket

PATH = "services.customers.classification.get_completion"


class PromptTests(SimpleTestCase):
    def test_the_options_block_names_every_value_in_the_taxonomy(self):
        """Built from taxonomy.py rather than written out, so a new category
        can't be one the model is never told about."""
        block = _options_block()

        for value in taxonomy.AICategory.values:
            self.assertIn(value, block)
        for value in taxonomy.AISubcategory.values:
            self.assertIn(value, block)
        for value in taxonomy.AIArea.values:
            self.assertIn(value, block)

    def test_subcategories_are_listed_under_their_own_category(self):
        line = next(
            line for line in _options_block().splitlines() if line.startswith("- bug_report")
        )

        self.assertIn("ui_bug", line)
        self.assertNotIn("api_issue", line)

    def test_long_text_is_truncated(self):
        prompt = build_prompt([("email:1", "Email", "x" * (MAX_TEXT_CHARS + 500))])

        self.assertIn("x" * MAX_TEXT_CHARS, prompt)
        self.assertNotIn("x" * (MAX_TEXT_CHARS + 1), prompt)


class ClassifyBatchTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.customer = Customer.objects.create(organisation=self.org, name="Apple Inc")
        self.email = Email.objects.create(
            customer=self.customer,
            subject="Export to CSV is missing columns",
            sender_name="Ada",
            recipient_name="Support",
            body="Every export drops the last three columns.",
            sent_at=timezone.now(),
        )

    def _answer(self, payload):
        return patch(PATH, return_value=payload)

    def test_a_clean_answer_is_read_straight_through(self):
        answer = (
            """[{"ref": "email:%s", "area": "product_growth",
            "category": "reporting_analytics", "subcategory": "export_problem",
            "sentiment": "negative"}]"""
            % self.email.pk
        )

        with self._answer(answer):
            results = classify_batch([self.email])

        self.assertEqual(
            results[f"email:{self.email.pk}"],
            {
                "ai_area": "product_growth",
                "ai_category": "reporting_analytics",
                "ai_subcategory": "export_problem",
                "sentiment": "negative",
            },
        )

    def test_human_labels_are_accepted_in_place_of_values(self):
        """The prompt shows both halves; a model will sometimes answer with the
        one that reads like English, and that answer is still correct."""
        answer = (
            """[{"ref": "email:%s", "area": "Product & Growth",
            "category": "Reporting & Analytics", "subcategory": "Export Problem",
            "sentiment": "Negative"}]"""
            % self.email.pk
        )

        with self._answer(answer):
            results = classify_batch([self.email])

        fields = results[f"email:{self.email.pk}"]
        self.assertEqual(fields["ai_category"], "reporting_analytics")
        self.assertEqual(fields["sentiment"], "negative")

    def test_a_subcategory_under_the_wrong_category_repairs_the_category(self):
        """The subcategory is the more specific claim and its parent is
        unambiguous, so it wins — rather than the pair being dropped."""
        answer = (
            """[{"ref": "email:%s", "category": "onboarding",
            "subcategory": "export_problem"}]"""
            % self.email.pk
        )

        with self._answer(answer):
            fields = classify_batch([self.email])[f"email:{self.email.pk}"]

        self.assertEqual(fields["ai_category"], "reporting_analytics")
        self.assertEqual(fields["ai_subcategory"], "export_problem")

    def test_an_invented_bucket_is_dropped_without_losing_the_batch(self):
        other = Email.objects.create(
            customer=self.customer,
            subject="All good",
            sender_name="Ada",
            recipient_name="Support",
            body="Thanks!",
            sent_at=timezone.now(),
        )
        answer = """[
            {"ref": "email:%s", "category": "vibes", "subcategory": "unclear"},
            {"ref": "email:%s", "category": "customer_feedback", "subcategory": "product_praise"}
        ]""" % (self.email.pk, other.pk)

        with self._answer(answer):
            results = classify_batch([self.email, other])

        self.assertNotIn(f"email:{self.email.pk}", results)
        self.assertIn(f"email:{other.pk}", results)

    def test_an_unknown_area_is_blanked_rather_than_sinking_the_item(self):
        answer = (
            """[{"ref": "email:%s", "area": "Vibes Department",
            "category": "bug_report", "subcategory": "ui_bug"}]"""
            % self.email.pk
        )

        with self._answer(answer):
            fields = classify_batch([self.email])[f"email:{self.email.pk}"]

        self.assertEqual(fields["ai_area"], "")
        self.assertEqual(fields["ai_category"], "bug_report")

    def test_a_ref_echoed_with_the_prompts_brackets_still_matches(self):
        """The first real run: the model answered every ticket as
        "[ticket:401]", brackets and all, and every one was discarded as a
        ref nobody sent — 600 correct answers reported as "unplaced"."""
        answer = (
            '[{"ref": "[email:%d]", "area": "product_growth", "category": "bug_report",'
            ' "subcategory": "ui_bug", "sentiment": "neutral"}]' % self.email.pk
        )

        with self._answer(answer):
            results = classify_batch([self.email])

        self.assertIn(f"email:{self.email.pk}", results)

    def test_an_unmatched_ref_is_logged_not_swallowed(self):
        answer = """[{"ref": "email:999999", "category": "bug_report",
            "subcategory": "ui_bug"}]"""

        with (
            self._answer(answer),
            self.assertLogs("services.customers.classification", "WARNING") as logs,
        ):
            classify_batch([self.email])

        self.assertIn("email:999999", logs.output[0])

    def test_a_ref_that_was_not_sent_is_discarded(self):
        """Otherwise a hallucinated id lets one batch write a classification
        onto a record nobody asked about."""
        answer = """[{"ref": "email:999999", "category": "bug_report",
            "subcategory": "ui_bug"}]"""

        with self._answer(answer):
            results = classify_batch([self.email])

        self.assertEqual(results, {})

    def test_a_fenced_answer_is_parsed(self):
        answer = '```json\n[{"ref": "email:%s", "category": "bug_report"}]\n```' % self.email.pk

        with self._answer(answer):
            results = classify_batch([self.email])

        self.assertEqual(results[f"email:{self.email.pk}"]["ai_category"], "bug_report")

    def test_an_array_wrapped_in_an_object_is_parsed(self):
        answer = '{"interactions": [{"ref": "email:%s", "category": "bug_report"}]}' % self.email.pk

        with self._answer(answer):
            results = classify_batch([self.email])

        self.assertEqual(len(results), 1)

    def test_a_non_json_answer_raises(self):
        with self._answer("I'd rather not."), self.assertRaises(ValueError):
            classify_batch([self.email])

    def test_a_missing_sentiment_defaults_to_neutral(self):
        answer = '[{"ref": "email:%s", "category": "bug_report"}]' % self.email.pk

        with self._answer(answer):
            fields = classify_batch([self.email])[f"email:{self.email.pk}"]

        self.assertEqual(fields["sentiment"], taxonomy.Sentiment.NEUTRAL)

    def test_the_output_budget_grows_with_the_batch(self):
        """The first real run: 1024 output tokens for twenty pretty-printed
        answers cut every reply off around line 128, and eight consecutive
        batches failed as "not JSON". The budget has to be sized to the list."""
        self.assertGreater(classification.output_budget(BATCH_SIZE), 1024)
        self.assertGreater(
            classification.output_budget(BATCH_SIZE), classification.output_budget(1)
        )

    def test_a_batch_call_passes_its_budget_to_the_model(self):
        records = [
            Email(pk=n, subject="s", body="b", sender_name="a", recipient_name="b")
            for n in range(BATCH_SIZE)
        ]
        with patch(PATH, return_value="[]") as completion:
            classification.classify_batch(records)

        self.assertEqual(
            completion.call_args.kwargs["max_tokens"], classification.output_budget(BATCH_SIZE)
        )

    def test_the_prompt_asks_for_one_line_per_answer(self):
        # Half the reason the budget ran out was indentation.
        self.assertIn("single line", classification.SYSTEM_PROMPT)

    def test_apply_classification_stamps_the_time(self):
        apply_classification(self.email, {"ai_category": "bug_report"})

        self.email.refresh_from_db()
        self.assertEqual(self.email.ai_category, "bug_report")
        self.assertIsNotNone(self.email.ai_classified_at)
        self.assertTrue(self.email.is_classified)


class ClassifyCommandTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.admin = User.objects.create_user(
            email="alice@acme.io",
            password="supersecret1",
            name="Alice",
            organisation=self.org,
            role=User.Role.ADMIN,
        )
        self.customer = Customer.objects.create(organisation=self.org, name="Apple Inc")
        self.emails = [
            Email.objects.create(
                customer=self.customer,
                subject=f"Subject {n}",
                sender_name="Ada",
                recipient_name="Support",
                body="Body.",
                sent_at=timezone.now(),
            )
            for n in range(3)
        ]

    def _run(self, **kwargs):
        out, err = StringIO(), StringIO()
        call_command("classify_interactions", stdout=out, stderr=err, **kwargs)
        return out.getvalue(), err.getvalue()

    def _batch_answer(self, records):
        return {
            f"{r._meta.model_name}:{r.pk}": {
                "ai_area": "product_growth",
                "ai_category": "bug_report",
                "ai_subcategory": "ui_bug",
                "sentiment": "neutral",
            }
            for r in records
        }

    def test_dry_run_writes_nothing_and_calls_nothing(self):
        with patch(PATH) as completion:
            out, _ = self._run(dry_run=True)

        completion.assert_not_called()
        self.assertIn("Would classify 3", out)
        self.assertFalse(Email.objects.filter(ai_classified_at__isnull=False).exists())

    def test_it_classifies_the_unclassified(self):
        with patch(
            "services.customers.management.commands.classify_interactions.classify_batch",
            side_effect=lambda batch, **_kw: self._batch_answer(batch),
        ):
            out, _ = self._run()

        self.assertIn("classified 3 of 3", out)
        self.assertEqual(Email.objects.filter(ai_category="bug_report").count(), 3)

    def test_already_classified_rows_are_skipped(self):
        apply_classification(self.emails[0], {"ai_category": "onboarding"})

        with patch(
            "services.customers.management.commands.classify_interactions.classify_batch",
            side_effect=lambda batch, **_kw: self._batch_answer(batch),
        ) as classify:
            self._run()

        sent = [r.pk for call in classify.call_args_list for r in call.args[0]]
        self.assertNotIn(self.emails[0].pk, sent)
        self.emails[0].refresh_from_db()
        self.assertEqual(self.emails[0].ai_category, "onboarding")

    def test_reclassify_redoes_them(self):
        apply_classification(self.emails[0], {"ai_category": "onboarding"})

        with patch(
            "services.customers.management.commands.classify_interactions.classify_batch",
            side_effect=lambda batch, **_kw: self._batch_answer(batch),
        ):
            self._run(reclassify=True)

        self.emails[0].refresh_from_db()
        self.assertEqual(self.emails[0].ai_category, "bug_report")

    def test_reclassify_clears_a_record_the_model_can_no_longer_place(self):
        """The first real run against the demo book: two calls with no summary
        were declined, and without this they kept the seeder's invented
        category with a fresh-looking stamp."""
        apply_classification(
            self.emails[0], {"ai_category": "onboarding", "ai_area": "customer_success"}
        )
        stale_stamp = self.emails[0].ai_classified_at

        def all_but_the_first(batch):
            return {
                k: v
                for k, v in self._batch_answer(batch).items()
                if k != f"email:{self.emails[0].pk}"
            }

        with patch(
            "services.customers.management.commands.classify_interactions.classify_batch",
            side_effect=lambda batch, **_kw: all_but_the_first(batch),
        ):
            out, _ = self._run(reclassify=True)

        self.assertIn("1 left unplaced", out)
        self.emails[0].refresh_from_db()
        self.assertEqual(self.emails[0].ai_category, "")
        self.assertEqual(self.emails[0].ai_area, "")
        # Looked at, so a scheduled default pass won't pay to retry it.
        self.assertIsNotNone(self.emails[0].ai_classified_at)
        self.assertGreater(self.emails[0].ai_classified_at, stale_stamp)

    def test_a_default_pass_leaves_an_unplaced_record_untouched(self):
        # Nothing to clear: it was blank, and stays retryable.
        with patch(
            "services.customers.management.commands.classify_interactions.classify_batch",
            side_effect=lambda batch, **_kw: {},
        ):
            self._run()

        self.emails[0].refresh_from_db()
        self.assertIsNone(self.emails[0].ai_classified_at)

    def test_limit_caps_the_spend(self):
        with patch(
            "services.customers.management.commands.classify_interactions.classify_batch",
            side_effect=lambda batch, **_kw: self._batch_answer(batch),
        ):
            self._run(limit=1)

        self.assertEqual(Email.objects.filter(ai_classified_at__isnull=False).count(), 1)

    def test_only_limits_the_record_type(self):
        Ticket.objects.create(
            customer=self.customer,
            ticket_number="TKT-1",
            title="Broken",
            assignee_name="Support",
            priority=Ticket.Priority.LOW,
            opened_at=timezone.localdate(),
        )

        with patch(
            "services.customers.management.commands.classify_interactions.classify_batch",
            side_effect=lambda batch, **_kw: self._batch_answer(batch),
        ):
            self._run(only=["ticket"])

        self.assertEqual(Ticket.objects.filter(ai_classified_at__isnull=False).count(), 1)
        self.assertFalse(Email.objects.filter(ai_classified_at__isnull=False).exists())

    def test_a_missing_api_key_stops_rather_than_failing_every_batch(self):
        with patch(PATH, side_effect=CopilotNotConfigured("Set ANTHROPIC_API_KEY.")):
            with self.assertRaises(CommandError) as caught:
                self._run()

        self.assertIn("ANTHROPIC_API_KEY", str(caught.exception))

    def test_one_failing_batch_does_not_abandon_the_rest(self):
        calls = {"n": 0}

        def flaky(batch, **_kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise CopilotRequestFailed("rate limited")
            return self._batch_answer(batch)

        with (
            patch("services.customers.classification.BATCH_SIZE", 1),
            patch("services.customers.management.commands.classify_interactions.BATCH_SIZE", 1),
            patch(
                "services.customers.management.commands.classify_interactions.classify_batch",
                side_effect=flaky,
            ),
        ):
            out, err = self._run()

        self.assertIn("rate limited", err)
        self.assertIn("1 batch(es) failed", out)
        self.assertEqual(Email.objects.filter(ai_classified_at__isnull=False).count(), 2)

    def test_an_unknown_org_email_is_a_clear_error(self):
        with self.assertRaises(CommandError):
            self._run(org_email="nobody@nowhere.io")

    def test_nothing_to_do_says_so(self):
        Email.objects.all().update(ai_classified_at=timezone.now())

        with patch(PATH) as completion:
            out, _ = self._run()

        completion.assert_not_called()
        self.assertIn("Nothing to classify", out)


class DemoClassificationTests(TestCase):
    """The free, keyword-driven seed. Its whole value is being predictable, so
    the rules that matter are asserted rather than eyeballed on a chart."""

    def test_keywords_place_a_record_where_a_reader_would(self):
        category, subcategory, _area = classify_text(
            "Export to CSV not including all columns", fallback_key=1
        )

        self.assertEqual(category, taxonomy.AICategory.REPORTING_ANALYTICS)
        self.assertEqual(subcategory, taxonomy.AISubcategory.EXPORT_PROBLEM)

    def test_the_more_specific_rule_wins(self):
        """ "webhook" is reached before the generic "integration"."""
        _category, subcategory, _area = classify_text(
            "Webhook integration keeps failing", fallback_key=1
        )

        self.assertEqual(subcategory, taxonomy.AISubcategory.WEBHOOK_FAILURE)

    def test_every_rule_produces_a_consistent_pair(self):
        """A keyword rule that paired a subcategory with the wrong category
        would be rejected by the model's own clean() at save time."""
        from services.customers.management.commands.seed_demo_classifications import (
            FALLBACK,
            KEYWORD_RULES,
        )

        pairs = [(c, s) for _keywords, c, s, _a in KEYWORD_RULES] + [
            (c, s) for c, s, _a in FALLBACK
        ]
        for category, subcategory in pairs:
            self.assertIsNone(
                taxonomy.validate_classification(ai_category=category, ai_subcategory=subcategory),
                f"{subcategory} is not under {category}",
            )

    def test_unmatched_text_still_gets_a_bucket(self):
        category, _subcategory, _area = classify_text("asdf", fallback_key=3)

        self.assertIn(category, taxonomy.AICategory.values)

    def test_a_deliberate_sentiment_is_left_alone(self):
        """A ticket the ticket seeder marked negative keeps it, so the Ticket
        Overview dashboard's numbers don't move underneath it."""
        kept = sentiment_for(
            "Thanks, this is great", current=taxonomy.Sentiment.NEGATIVE, fallback_key=1
        )

        self.assertEqual(kept, taxonomy.Sentiment.NEGATIVE)

    def test_negative_words_win_over_positive_ones(self):
        self.assertEqual(
            sentiment_for(
                "Thanks, but this escalation is unacceptable",
                current=taxonomy.Sentiment.NEUTRAL,
                fallback_key=1,
            ),
            taxonomy.Sentiment.NEGATIVE,
        )

    def test_the_command_fills_in_every_type_and_is_idempotent(self):
        org = Organisation.objects.create(name="Acme Inc")
        User.objects.create_user(
            email="alice@acme.io",
            password="supersecret1",
            name="Alice",
            organisation=org,
            role=User.Role.ADMIN,
        )
        customer = Customer.objects.create(organisation=org, name="Apple Inc")
        Email.objects.create(
            customer=customer,
            subject="Webhook failures again",
            sender_name="Ada",
            recipient_name="Support",
            body="Retries keep bouncing.",
            sent_at=timezone.now(),
        )
        Call.objects.create(
            customer=customer,
            title="Onboarding Kickoff",
            host_name="Ada",
            occurred_at=timezone.now(),
        )
        Ticket.objects.create(
            customer=customer,
            ticket_number="TKT-1",
            title="Dashboard loading slow on large datasets",
            assignee_name="Support",
            priority=Ticket.Priority.HIGH,
            opened_at=timezone.localdate(),
        )

        out = StringIO()
        call_command("seed_demo_classifications", org_email="alice@acme.io", stdout=out)

        self.assertEqual(Email.objects.filter(ai_classified_at__isnull=False).count(), 1)
        self.assertEqual(
            Ticket.objects.get().ai_subcategory, taxonomy.AISubcategory.PERFORMANCE_ISSUE
        )
        self.assertEqual(Call.objects.get().ai_category, taxonomy.AICategory.ONBOARDING)

        # Second run: nothing left unclassified, so nothing is rewritten.
        again = StringIO()
        call_command("seed_demo_classifications", org_email="alice@acme.io", stdout=again)
        self.assertIn("0 email(s)", again.getvalue())

    def test_every_seeded_classification_survives_full_clean(self):
        """The pairs are written straight onto rows with `save()`, so nothing
        would otherwise catch a rule that violates the taxonomy."""
        org = Organisation.objects.create(name="Acme Inc")
        User.objects.create_user(
            email="alice@acme.io",
            password="supersecret1",
            name="Alice",
            organisation=org,
            role=User.Role.ADMIN,
        )
        customer = Customer.objects.create(organisation=org, name="Apple Inc")
        for subject in ["Export problem", "SSO login", "Random chatter"]:
            Email.objects.create(
                customer=customer,
                subject=subject,
                sender_name="Ada",
                recipient_name="Support",
                body="Some body text.",
                sent_at=timezone.now(),
            )

        call_command("seed_demo_classifications", org_email="alice@acme.io", stdout=StringIO())

        for email in Email.objects.all():
            email.full_clean()
