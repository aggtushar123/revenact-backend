"""The AI Trending Topics dashboard: the taxonomy, the Call model, and the one
endpoint behind all seven of its charts.

Three tiers, in that order — the vocabulary with no database, the models, then
the endpoint. The classifier that *writes* the taxonomy is covered in
test_classification.py; nothing here calls a model provider.
"""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.connectors.models import Connector
from services.customers import taxonomy
from services.customers.interactions import REVENUE_BRACKETS
from services.customers.models import Account, Call, Customer, Email, Ticket


class TaxonomyTests(SimpleTestCase):
    def test_every_subcategory_belongs_to_exactly_one_category(self):
        """The dashboard reads the Category and Subcategory bars together, so a
        subcategory under two parents would make one of the two wrong."""
        seen = [sub for subs in taxonomy.SUBCATEGORIES_BY_CATEGORY.values() for sub in subs]
        self.assertEqual(len(seen), len(set(seen)))
        self.assertEqual(set(seen), set(taxonomy.AISubcategory))

    def test_area_is_not_derived_from_category(self):
        """Three areas and ten categories, independently — see taxonomy.py on
        why "who owns this" and "what is it about" are two questions."""
        self.assertEqual(len(taxonomy.AIArea.choices), 3)
        self.assertEqual(len(taxonomy.AICategory.choices), 10)

    def test_a_consistent_pair_validates(self):
        self.assertIsNone(
            taxonomy.validate_classification(
                ai_category=taxonomy.AICategory.BUG_REPORT,
                ai_subcategory=taxonomy.AISubcategory.UI_BUG,
            )
        )

    def test_a_subcategory_from_another_category_is_rejected(self):
        error = taxonomy.validate_classification(
            ai_category=taxonomy.AICategory.ONBOARDING,
            ai_subcategory=taxonomy.AISubcategory.API_ISSUE,
        )
        self.assertIn("API Issue", error)
        self.assertIn("Onboarding", error)

    def test_a_category_with_no_subcategory_is_allowed(self):
        """ "A bug report, and we don't know more than that" is a real state."""
        self.assertIsNone(
            taxonomy.validate_classification(
                ai_category=taxonomy.AICategory.BUG_REPORT, ai_subcategory=""
            )
        )

    def test_a_subcategory_with_no_category_is_rejected(self):
        self.assertIsNotNone(
            taxonomy.validate_classification(
                ai_category="", ai_subcategory=taxonomy.AISubcategory.UI_BUG
            )
        )

    def test_sentiment_is_the_same_three_values_everywhere(self):
        from services.customers.models import Contact

        self.assertEqual(
            [v for v, _ in taxonomy.Sentiment.choices],
            [v for v, _ in Contact.Sentiment.choices],
        )

    def test_ticket_still_exposes_sentiment_under_its_old_name(self):
        """~30 existing call sites say `Ticket.Sentiment.POSITIVE`."""
        self.assertEqual(Ticket.Sentiment.POSITIVE, taxonomy.Sentiment.POSITIVE)


class CallModelTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.customer = Customer.objects.create(organisation=self.org, name="Apple Inc")
        self.account = Account.objects.create(name="Apple EMEA")
        self.account.customers.add(self.customer)

    def _call(self, **overrides):
        return Call(
            **{
                "customer": self.customer,
                "title": "Quarterly Business Review",
                "host_name": "Chamath Gamage",
                "occurred_at": timezone.now(),
                **overrides,
            }
        )

    def test_a_call_needs_exactly_one_parent(self):
        from django.db.utils import IntegrityError

        with self.assertRaises(IntegrityError):
            Call.objects.create(
                customer=self.customer,
                account=self.account,
                title="Both",
                host_name="Nobody",
                occurred_at=timezone.now(),
            )

    def test_duration_may_be_unknown(self):
        """Null is "the recorder didn't say", which is not a zero-minute call."""
        call = self._call(duration_minutes=None)
        call.full_clean()
        call.save()
        self.assertIsNone(Call.objects.get(pk=call.pk).duration_minutes)

    def test_a_mismatched_subcategory_is_rejected(self):
        call = self._call(
            ai_category=taxonomy.AICategory.ONBOARDING,
            ai_subcategory=taxonomy.AISubcategory.API_ISSUE,
        )
        with self.assertRaises(ValidationError) as caught:
            call.full_clean()
        self.assertIn("ai_subcategory", caught.exception.error_dict)

    def test_a_recorder_that_does_not_cover_the_company_is_rejected(self):
        """The same invariant Ticket.clean() enforces, in the same place."""
        other = Customer.objects.create(organisation=self.org, name="Pizza Hut")
        recorder = Connector.objects.create(
            organisation=self.org, provider=Connector.Provider.ZOOM, name="Zoom"
        )
        recorder.customers.add(other)

        with self.assertRaises(ValidationError) as caught:
            self._call(connector=recorder).full_clean()
        self.assertIn("connector", caught.exception.error_dict)

    def test_an_org_wide_recorder_covers_everyone(self):
        recorder = Connector.objects.create(
            organisation=self.org, provider=Connector.Provider.ZOOM, name="Zoom"
        )
        self._call(connector=recorder).full_clean()

    def test_is_classified_tracks_the_stamp_not_the_fields(self):
        call = self._call(ai_category=taxonomy.AICategory.BUG_REPORT)
        self.assertFalse(call.is_classified)
        call.ai_classified_at = timezone.now()
        self.assertTrue(call.is_classified)


class InteractionStatsTests(APITestCase):
    """Every bucket is asserted against a known fixture rather than "a number
    came back": an aggregation that groups wrongly still returns a well-formed
    200 — see the `.order_by()` the Ticket dashboard shipped without."""

    url = "/api/v1/interactions/stats/"

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.csm = User.objects.create_user(
            email="carl@acme.io",
            password="supersecret1",
            name="Carl",
            organisation=self.org,
            role=User.Role.CSM,
        )
        self.other = User.objects.create_user(
            email="dana@acme.io",
            password="supersecret1",
            name="Dana",
            organisation=self.org,
            role=User.Role.CSM,
        )
        self.mine = Customer.objects.create(
            organisation=self.org,
            name="Mine",
            owner=self.csm,
            arr_billed_at_account=100_000,
        )
        self.theirs = Customer.objects.create(
            organisation=self.org, name="Theirs", owner=self.other
        )
        self.client.force_authenticate(self.csm)

    # ── fixtures ─────────────────────────────────────────────────────

    def _email(self, n=1, **overrides):
        return Email.objects.create(
            **{
                "customer": self.mine,
                "subject": f"Checking in {n}",
                "sender_name": "Ada",
                "recipient_name": "Customer",
                "body": "Body text.",
                "sent_at": timezone.now(),
                **overrides,
            }
        )

    def _call(self, n=1, **overrides):
        return Call.objects.create(
            **{
                "customer": self.mine,
                "title": f"Review {n}",
                "host_name": "Ada",
                "occurred_at": timezone.now(),
                **overrides,
            }
        )

    def _ticket(self, n=1, **overrides):
        return Ticket.objects.create(
            **{
                "customer": self.mine,
                "ticket_number": f"TKT-{n}",
                "title": "Something broke",
                "assignee_name": "Support Team",
                "priority": Ticket.Priority.HIGH,
                "opened_at": timezone.localdate(),
                **overrides,
            }
        )

    def _classified(self, record, **overrides):
        record.ai_area = overrides.get("area", taxonomy.AIArea.PRODUCT_GROWTH)
        record.ai_category = overrides.get("category", taxonomy.AICategory.BUG_REPORT)
        record.ai_subcategory = overrides.get("subcategory", taxonomy.AISubcategory.UI_BUG)
        record.ai_classified_at = timezone.now()
        record.save()
        return record

    # ── access ───────────────────────────────────────────────────────

    def test_unauthenticated_is_rejected(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_another_owners_interactions_are_invisible(self):
        self._email(1)
        self._email(2, customer=self.theirs)

        data = self.client.get(self.url).data

        self.assertEqual(data["total"], 1)

    # ── the source donut ─────────────────────────────────────────────

    def test_source_buckets_sum_to_the_total(self):
        """The guard against Django folding each model's Meta.ordering into the
        GROUP BY, which makes every bucket count exactly 1."""
        for n in range(5):
            self._email(n)
        for n in range(3):
            self._call(n)
        self._ticket(1)

        data = self.client.get(self.url).data

        by_type = {row["name"]: row["value"] for row in data["by_type"]}
        self.assertEqual(by_type, {"Email": 5, "Call": 3, "Ticket": 1})
        self.assertEqual(sum(row["value"] for row in data["by_type"]), data["total"])

    def test_all_three_sources_appear_even_at_zero(self):
        self._email(1)

        names = {row["name"] for row in self.client.get(self.url).data["by_type"]}

        self.assertEqual(names, {"Email", "Call", "Ticket"})

    # ── sentiment ────────────────────────────────────────────────────

    def test_sentiment_counts_every_record_across_all_three_models(self):
        self._email(1, sentiment=taxonomy.Sentiment.POSITIVE)
        self._call(1, sentiment=taxonomy.Sentiment.POSITIVE)
        self._ticket(1, sentiment=taxonomy.Sentiment.NEGATIVE)

        sentiment = {r["name"]: r["value"] for r in self.client.get(self.url).data["sentiment"]}

        self.assertEqual(sentiment["Positive"], 2)
        self.assertEqual(sentiment["Negative"], 1)
        self.assertEqual(sentiment["Neutral"], 0)

    def test_the_weekly_trend_buckets_by_when_it_happened(self):
        """Not by created_at, which is auto_now_add — every seeded row shares
        one timestamp and a trend over it is a single spike."""
        now = timezone.now()
        self._email(1, sent_at=now, sentiment=taxonomy.Sentiment.POSITIVE)
        self._email(2, sent_at=now - timedelta(days=21), sentiment=taxonomy.Sentiment.NEGATIVE)

        timeline = self.client.get(self.url).data["sentiment_timeline"]

        self.assertEqual(len(timeline), 2)
        self.assertEqual(timeline[0]["negative"], 1)
        self.assertEqual(timeline[-1]["positive"], 1)
        # The label is pre-formatted for the axis, day not zero-padded.
        self.assertRegex(timeline[0]["date"], r"^[A-Z][a-z]{2} \d{1,2}, \d{4}$")

    # ── the taxonomy charts ──────────────────────────────────────────

    def test_unclassified_rows_count_in_the_total_but_not_in_the_ai_charts(self):
        self._classified(self._email(1))
        self._email(2)  # never classified

        data = self.client.get(self.url).data

        self.assertEqual(data["total"], 2)
        self.assertEqual(data["classified"], 1)
        self.assertEqual(sum(r["value"] for r in data["categories"]), 1)
        self.assertEqual(sum(r["value"] for r in data["areas"]), 1)

    def test_categories_are_ranked_biggest_first_with_empties_dropped(self):
        for n in range(3):
            self._classified(
                self._email(n),
                category=taxonomy.AICategory.BUG_REPORT,
                subcategory=taxonomy.AISubcategory.UI_BUG,
            )
        self._classified(
            self._call(1),
            category=taxonomy.AICategory.ONBOARDING,
            subcategory=taxonomy.AISubcategory.SETUP_ASSISTANCE,
        )

        categories = self.client.get(self.url).data["categories"]

        self.assertEqual([r["name"] for r in categories], ["Bug Report", "Onboarding"])
        self.assertEqual([r["value"] for r in categories], [3, 1])

    def test_areas_keep_every_bucket_for_a_stable_legend(self):
        self._classified(self._email(1), area=taxonomy.AIArea.PRODUCT_GROWTH)

        areas = self.client.get(self.url).data["areas"]

        self.assertEqual(len(areas), 3)
        self.assertEqual({r["name"] for r in areas}, {c[1] for c in taxonomy.AIArea.choices})

    # ── filters ──────────────────────────────────────────────────────

    def test_type_filter_drops_whole_record_types(self):
        self._email(1)
        self._call(1)
        self._ticket(1)

        data = self.client.get(self.url, {"type": "call"}).data

        self.assertEqual(data["total"], 1)
        self.assertEqual([r["name"] for r in data["by_type"]], ["Call"])

    def test_type_filter_is_repeatable(self):
        self._email(1)
        self._call(1)
        self._ticket(1)

        data = self.client.get(f"{self.url}?type=call&type=ticket").data

        self.assertEqual(data["total"], 2)

    def test_an_unknown_type_is_ignored_rather_than_returning_nothing(self):
        """A dashboard should draw with the filters it understood."""
        self._email(1)

        self.assertEqual(self.client.get(self.url, {"type": "smoke-signal"}).data["total"], 1)

    def test_sentiment_and_taxonomy_filters_narrow_rows(self):
        self._classified(self._email(1, sentiment=taxonomy.Sentiment.NEGATIVE))
        self._classified(
            self._email(2),
            category=taxonomy.AICategory.ONBOARDING,
            subcategory=taxonomy.AISubcategory.SETUP_ASSISTANCE,
        )

        self.assertEqual(self.client.get(self.url, {"sentiment": "negative"}).data["total"], 1)
        self.assertEqual(self.client.get(self.url, {"category": "bug_report"}).data["total"], 1)
        self.assertEqual(self.client.get(self.url, {"subcategory": "ui_bug"}).data["total"], 1)
        self.assertEqual(self.client.get(self.url, {"area": "product_growth"}).data["total"], 2)

    def test_a_bad_filter_value_is_ignored(self):
        self._email(1)

        self.assertEqual(self.client.get(self.url, {"sentiment": "grumpy"}).data["total"], 1)
        self.assertEqual(self.client.get(self.url, {"category": "nonsense"}).data["total"], 1)
        self.assertEqual(self.client.get(self.url, {"customer": "abc"}).data["total"], 1)

    def test_the_date_window_works_across_both_date_and_datetime_fields(self):
        """Email/Call carry a timestamp and Ticket a plain date — the filter has
        to reach both without asking a DateField for a `__date` transform."""
        old = timezone.now() - timedelta(days=40)
        self._email(1, sent_at=old)
        self._call(1, occurred_at=old)
        self._ticket(1, opened_at=(timezone.localdate() - timedelta(days=40)))
        self._email(2)
        self._ticket(2, ticket_number="TKT-new")

        cutoff = (timezone.localdate() - timedelta(days=7)).isoformat()
        data = self.client.get(self.url, {"from": cutoff}).data

        self.assertEqual(data["total"], 2)

    def test_the_customer_filter_includes_its_accounts_interactions(self):
        account = Account.objects.create(name="Mine EMEA", owner=self.csm)
        account.customers.add(self.mine)
        self._email(1)
        self._email(2, customer=None, account=account)

        data = self.client.get(self.url, {"customer": self.mine.pk}).data

        self.assertEqual(data["total"], 2)

    def test_the_revenue_bracket_filter_reads_the_parents_arr(self):
        small = Customer.objects.create(
            organisation=self.org, name="Small", owner=self.csm, arr_billed_at_account=10_000
        )
        self._email(1)  # self.mine, ARR 100k
        self._email(2, customer=small)

        # Brackets are floor-inclusive, ceiling-exclusive, so 100k lands in the
        # top bracket rather than in "$50K – $100K" below it.
        self.assertEqual(
            self.client.get(self.url, {"revenue_bracket": "over_100k"}).data["total"], 1
        )
        self.assertEqual(
            self.client.get(self.url, {"revenue_bracket": "under_25k"}).data["total"], 1
        )
        self.assertEqual(
            self.client.get(self.url, {"revenue_bracket": "50k_100k"}).data["total"], 0
        )

    def test_an_account_level_row_is_reachable_by_revenue_bracket(self):
        """The filter has to name both parent shapes or it silently drops every
        account-level interaction."""
        account = Account.objects.create(name="Mine EMEA", owner=self.csm, arr=250_000)
        account.customers.add(self.mine)
        self._email(1, customer=None, account=account)

        data = self.client.get(self.url, {"revenue_bracket": "over_100k"}).data

        self.assertEqual(data["total"], 1)

    # ── the detail table and the filter options ──────────────────────

    def test_recent_rows_carry_display_labels_and_the_parents_name(self):
        self._classified(self._email(1))

        row = self.client.get(self.url).data["recent"][0]

        self.assertEqual(row["source"], "Email")
        self.assertEqual(row["account"], "Mine")
        self.assertEqual(row["title"], "Checking in 1")
        self.assertEqual(row["category"], "Bug Report")
        self.assertEqual(row["subcategory"], "UI Bug")
        self.assertEqual(row["sentiment"], "Neutral")

    def test_recent_rows_are_newest_first_across_all_three_models(self):
        now = timezone.now()
        self._email(1, sent_at=now - timedelta(days=5))
        self._call(1, occurred_at=now)
        self._ticket(1, opened_at=timezone.localdate() - timedelta(days=2))

        sources = [row["source"] for row in self.client.get(self.url).data["recent"]]

        self.assertEqual(sources, ["Call", "Ticket", "Email"])

    def test_an_unclassified_row_still_appears_in_the_table(self):
        """Its taxonomy columns are blank, which is the truth — unlike the AI
        charts, the table is a list of what happened."""
        self._email(1)

        row = self.client.get(self.url).data["recent"][0]

        self.assertEqual(row["category"], "")
        self.assertEqual(row["area"], "")

    def test_filter_options_are_scoped_like_the_numbers(self):
        options = self.client.get(self.url).data["filters"]

        self.assertEqual([c["name"] for c in options["customers"]], ["Mine"])
        self.assertEqual(
            [b["value"] for b in options["revenue_brackets"]],
            [value for value, _label, _floor, _ceiling in REVENUE_BRACKETS],
        )

    def test_subcategory_options_carry_their_parent_category(self):
        """So the filter bar can narrow that dropdown to the chosen category
        instead of offering twenty-five values of which three can match."""
        options = self.client.get(self.url).data["filters"]["subcategories"]

        ui_bug = next(o for o in options if o["value"] == "ui_bug")
        self.assertEqual(ui_bug["category"], "bug_report")
        self.assertEqual(len(options), len(taxonomy.AISubcategory.choices))

    # ── drill ────────────────────────────────────────────────────────

    def test_drill_counts_interactions_per_company(self):
        self._email(1)
        self._email(2)
        self._call(1)

        body = self.client.get(self.url, {"drill": "all"}).json()

        self.assertEqual(set(body), {"drill", "currency"})
        self.assertEqual(
            [(c["name"], c["value"]) for c in body["drill"]["companies"]], [("Mine", 3)]
        )
        self.assertEqual(body["drill"]["value_label"], "interactions")

    def test_drill_by_type_and_by_taxonomy(self):
        self._email(1)
        call = self._call(1)
        self._classified(
            call,
            area=taxonomy.AIArea.CUSTOMER_SUCCESS,
            category=taxonomy.AICategory.ONBOARDING,
            subcategory=taxonomy.AISubcategory.SETUP_ASSISTANCE,
        )
        cases = {
            "type:email": 1,
            "type:call": 1,
            "area:customer_success": 1,
            "category:onboarding": 1,
            "subcategory:setup_assistance": 1,
        }
        for drill, expected in cases.items():
            with self.subTest(drill=drill):
                companies = self.client.get(self.url, {"drill": drill}).json()["drill"]["companies"]
                self.assertEqual([c["value"] for c in companies], [expected])

    def test_a_customer_filter_drills_to_that_customer_only(self):
        sibling = Customer.objects.create(organisation=self.org, name="Sibling", owner=self.csm)
        account = Account.objects.create(name="Shared")
        account.customers.add(self.mine, sibling)
        self._email(1, customer=None, account=account)

        both = self.client.get(self.url, {"drill": "all"}).json()["drill"]["companies"]
        self.assertEqual(sorted(c["name"] for c in both), ["Mine", "Sibling"])
        body = self.client.get(self.url, {"drill": "all", "customer": self.mine.pk}).json()
        self.assertEqual([c["name"] for c in body["drill"]["companies"]], ["Mine"])

    def test_drill_keeps_personal_mail_rules(self):
        """The same visible_emails rule the totals use: another person's
        mailbox mail is never counted towards a company."""
        self._email(1, mailbox_owner=self.other)

        companies = self.client.get(self.url, {"drill": "type:email"}).json()["drill"]["companies"]

        self.assertEqual(companies, [])

    def test_a_bad_drill_returns_the_normal_stats(self):
        self._email(1)

        for drill in [
            "type:fax",
            "sentiment:furious",
            "area:bogus",
            "category:bogus",
            "subcategory:bogus",
            "nope",
        ]:
            with self.subTest(drill=drill):
                self.assertNotIn("drill", self.client.get(self.url, {"drill": drill}).json())


class SeedDemoCallsTests(TestCase):
    """The seeder is demo plumbing, but two of its properties are load-bearing:
    it must be re-runnable, and every call it writes must satisfy the invariant
    Call.clean() enforces — a seed that wrote invalid rows would leave the demo
    database in a state the product can't produce."""

    def setUp(self):
        from io import StringIO

        self.out = StringIO()
        self.org = Organisation.objects.create(name="Acme Inc")
        User.objects.create_user(
            email="alice@acme.io",
            password="supersecret1",
            name="Alice",
            organisation=self.org,
            role=User.Role.ADMIN,
        )
        self.customer = Customer.objects.create(organisation=self.org, name="Apple Inc")
        self.account = Account.objects.create(name="Apple EMEA")
        self.account.customers.add(self.customer)

    def _seed(self, **kwargs):
        from django.core.management import call_command

        call_command("seed_demo_calls", org_email="alice@acme.io", stdout=self.out, **kwargs)

    def test_it_seeds_the_two_calls_the_callsense_mock_named(self):
        self._seed(volume=0)

        titles = set(Call.objects.values_list("title", flat=True))
        self.assertIn("EMEA Retail - Renewal Readiness Check-in", titles)
        self.assertTrue(all(call.account_id == self.account.pk for call in Call.objects.all()))

    def test_rerunning_updates_rather_than_duplicating(self):
        self._seed(volume=20)
        first = Call.objects.count()

        self._seed(volume=20)

        self.assertEqual(Call.objects.count(), first)

    def test_every_seeded_call_is_valid(self):
        from django.core.management import call_command

        call_command("seed_demo_connectors", org_email="alice@acme.io", stdout=self.out)
        self._seed(volume=10)

        for call in Call.objects.all():
            call.full_clean()

    def test_calls_are_attributed_to_the_org_wide_recorder_when_one_exists(self):
        from django.core.management import call_command

        call_command("seed_demo_connectors", org_email="alice@acme.io", stdout=self.out)
        self._seed(volume=5)

        providers = {call.connector.provider for call in Call.objects.all() if call.connector}
        self.assertEqual(providers, {Connector.Provider.ZOOM})

    def test_without_connectors_the_calls_are_logged_in_revenact(self):
        """Null connector is a real case — a call logged by hand — not an error."""
        self._seed(volume=5)

        self.assertTrue(all(call.connector_id is None for call in Call.objects.all()))
