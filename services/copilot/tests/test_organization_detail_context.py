"""The context of a question asked on one organisation's page: both ids are
re-checked against what the asker may open, the label is the server's, and a
focus survives only when the asker may read the item."""

from datetime import timedelta

from django.utils import timezone

from services.copilot.ask import AskContextSerializer
from services.copilot.organization_detail_context import NOT_AN_ACCOUNT, NOT_OPEN
from services.customers.models import Customer
from services.customers.tests.test_views import blind_to_one_account
from services.organizations.tests.story_fixtures import StoryFixture


class DetailContextTests(StoryFixture):
    def check(self, user=None, **context):
        serializer = AskContextSerializer(
            data={"surface": "organizations", "view": "detail", **context},
            context={"user": user or self.csm},
        )
        valid = serializer.is_valid()
        return valid, serializer.validated_data if valid else serializer.errors

    def test_the_organisation_alone_is_labelled_with_its_name(self):
        valid, data = self.check(organization=self.pizza.pk, label="Something else")

        self.assertTrue(valid, data)
        self.assertEqual(
            data,
            {
                "surface": "organizations",
                "view": "detail",
                "organization": self.pizza.pk,
                "account": None,
                "label": "Pizza Hut",
                "focus": None,
            },
        )

    def test_an_account_narrows_and_names_the_chip(self):
        valid, data = self.check(organization=self.pizza.pk, account=self.emea.pk)

        self.assertTrue(valid, data)
        self.assertEqual(data["account"], self.emea.pk)
        self.assertEqual(data["label"], "Pizza Hut · EMEA")

    def test_an_organisation_the_asker_cannot_open_reads_like_one_that_does_not_exist(self):
        for user, pk in ((self.other, self.pizza.pk), (self.csm, 999999)):
            with self.subTest(user=user.name, pk=pk):
                valid, errors = self.check(user, organization=pk)

                self.assertFalse(valid)
                self.assertEqual(errors, {"organization": [NOT_OPEN]})

    def test_the_organisation_is_required(self):
        valid, errors = self.check()

        self.assertFalse(valid)
        self.assertIn("organization", errors)

    def test_an_account_must_be_one_of_this_organisations_that_the_asker_may_open(self):
        hooli = self.customer("Hooli")
        elsewhere = self.account("Hooli EU", customers=[hooli])
        globex = Customer.objects.create(organisation=self.org, name="Globex")
        viewer, seen, hidden = blind_to_one_account(globex)
        cases = (
            (self.csm, self.pizza, elsewhere),
            (viewer, globex, hidden),
            (self.csm, self.pizza, None),
        )
        for user, customer, account in cases:
            with self.subTest(account=account and account.name):
                pk = account.pk if account else 999999
                valid, errors = self.check(user, organization=customer.pk, account=pk)

                self.assertFalse(valid)
                self.assertEqual(errors, {"account": [NOT_AN_ACCOUNT]})
        valid, data = self.check(viewer, organization=globex.pk, account=seen.pk)
        self.assertTrue(valid, data)
        self.assertEqual(data["label"], "Globex · Seen")

    def test_a_focus_on_an_item_the_asker_may_read_is_kept(self):
        note = self.note(self.emea)
        before = self.snapshot(self.pizza, self.days_ago(40), "8.0")
        after = self.snapshot(self.pizza, self.days_ago(10), "3.0")
        self.assertNotEqual(before.pk, after.pk)
        for kind, pk in (("note", note.pk), ("health", after.pk)):
            with self.subTest(kind=kind):
                valid, data = self.check(organization=self.pizza.pk, focus={"kind": kind, "id": pk})

                self.assertTrue(valid, data)
                self.assertEqual(data["focus"], {"kind": kind, "id": pk})

    def test_a_focus_the_asker_may_not_read_here_is_dropped_silently(self):
        private = self.note(self.pizza, title="Dana's own", author=self.other)
        on_apac = self.task(self.apac)
        hooli = self.customer("Hooli")
        elsewhere = self.ticket(hooli)
        future = self.email(self.pizza, at=timezone.now() + timedelta(days=2))
        cases = (
            ("note", private.pk, None),
            ("task", on_apac.pk, self.emea.pk),
            ("ticket", elsewhere.pk, None),
            ("email", future.pk, None),
            ("note", 999999, None),
        )
        for kind, pk, account in cases:
            with self.subTest(kind=kind, pk=pk):
                valid, data = self.check(
                    organization=self.pizza.pk, account=account, focus={"kind": kind, "id": pk}
                )

                self.assertTrue(valid, data)
                self.assertIsNone(data["focus"])

    def test_a_malformed_focus_is_a_400(self):
        for focus, field in (
            ({"kind": "companies", "id": 1}, "kind"),
            ({"kind": "note", "id": "x"}, "id"),
            ({"kind": "note"}, "id"),
        ):
            with self.subTest(focus=focus):
                valid, errors = self.check(organization=self.pizza.pk, focus=focus)

                self.assertFalse(valid)
                self.assertIn(field, errors["focus"])

    def test_the_list_and_board_still_validate_as_before(self):
        serializer = AskContextSerializer(
            data={"surface": "organizations", "view": "list", "filters": {"health": "poor"}},
            context={"user": self.csm},
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["filters"], {"health": "poor"})
        self.assertNotIn("organization", serializer.validated_data)
