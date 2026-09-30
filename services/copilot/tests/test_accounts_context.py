"""What the client may send from Accounts, and what is stored: the portfolio's
URL filters in one canonical form with a server-built label, or one account
and at most one story item — each 400 reading the same whether the id exists
or not."""

from datetime import timedelta

from services.accounts.models import User
from services.copilot.accounts_context import (
    NOT_A_STORY_ITEM,
    NOT_OPEN_ACCOUNT,
    NOT_OPEN_ORGANISATION,
    AccountsContextSerializer,
)
from services.organizations.tests.story_fixtures import StoryFixture


class AccountsContextFixture(StoryFixture):
    """StoryFixture: Carl owns Pizza Hut, whose EMEA and APAC accounts are
    unowned; Dana is another CSM; Alice the admin; Globex another tenant. The
    EMEA account is named as the page would show it, "Pizza Hut EMEA"."""

    def setUp(self):
        super().setUp()
        self.emea.name = "Pizza Hut EMEA"
        self.emea.save(update_fields=["name"])

    def check(self, data, user=None):
        serializer = AccountsContextSerializer(data=data, context={"user": user or self.csm})
        valid = serializer.is_valid()
        return valid, (serializer.validated_data if valid else serializer.errors)

    @staticmethod
    def listing(view="list", **filters):
        return {"surface": "accounts", "view": view, "filters": filters}

    @staticmethod
    def detail(account, focus=None):
        pk = getattr(account, "pk", account)
        return {"surface": "accounts", "view": "detail", "account": pk, "focus": focus}


class ListContextTests(AccountsContextFixture):
    def test_no_filters_is_the_whole_book(self):
        valid, data = self.check(self.listing())

        self.assertTrue(valid, data)
        self.assertEqual(
            dict(data),
            {"surface": "accounts", "view": "list", "filters": {}, "label": "Accounts"},
        )

    def test_filters_are_parsed_as_the_portfolio_parses_them_and_labelled(self):
        self.account("Carl's own", owner=self.csm)

        valid, data = self.check(
            {
                **self.listing(
                    health="poor,bogus",
                    owner=str(self.csm.pk),
                    lifecycle="live",
                    colour="red",
                    sort="-arr",
                    renews_within="45",
                ),
                "label": "Spoofed",
            }
        )

        self.assertTrue(valid, data)
        # Unknown keys and values are dropped; the default sort is left out;
        # 45 is not a renewal window the page offers.
        self.assertEqual(
            data["filters"], {"health": "poor", "owner": str(self.csm.pk), "lifecycle": "live"}
        )
        self.assertEqual(
            data["label"], "Accounts · Owner: Carl CSM · Lifecycle: Live · Health: Poor"
        )

    def test_every_label_in_list_order(self):
        valid, data = self.check(
            self.listing(
                ids=f"{self.emea.pk},{self.apac.pk}",
                search="emea",
                organisation=str(self.pizza.pk),
                owner="unassigned",
                renews_within="90",
                nps="detractor",
                sort="renewal",
                group="owner",
            )
        )

        self.assertTrue(valid, data)
        self.assertEqual(
            data["label"],
            'Accounts · Chosen accounts (2) · Search: "emea" · Organisation: Pizza Hut · '
            "Owner: Unassigned · Renews within 90 days · NPS: Detractors",
        )
        self.assertEqual(
            data["filters"],
            {
                "ids": f"{self.emea.pk},{self.apac.pk}",
                "search": "emea",
                "organisation": str(self.pizza.pk),
                "owner": "unassigned",
                "renews_within": "90",
                "nps": "detractor",
                "sort": "renewal",
                "group": "owner",
            },
        )

    def test_repeated_ids_are_deduped_so_the_label_matches_the_stored_filter(self):
        valid, data = self.check(self.listing(ids=f"{self.emea.pk},{self.apac.pk},{self.emea.pk}"))

        self.assertTrue(valid, data)
        self.assertEqual(data["label"], "Accounts · Chosen accounts (2)")
        self.assertEqual(data["filters"], {"ids": f"{self.emea.pk},{self.apac.pk}"})

    def test_an_owner_outside_the_askers_book_is_not_named(self):
        self.account(
            "Dana's", owner=self.other, customers=[self.customer("Taco", owner=self.other)]
        )

        valid, data = self.check(self.listing(owner=str(self.other.pk)))

        self.assertTrue(valid, data)
        self.assertEqual(data["label"], "Accounts · Owner: not in your book")
        self.assertEqual(data["filters"], {"owner": str(self.other.pk)})

    def test_an_explicit_empty_group_survives_as_not_grouped(self):
        valid, data = self.check(self.listing("board", group=""))

        self.assertTrue(valid, data)
        self.assertEqual(data["view"], "board")
        self.assertEqual(data["filters"], {"group": ""})

    def test_an_overlong_search_is_dropped_not_rejected(self):
        valid, data = self.check(self.listing(search="x" * 101))

        self.assertTrue(valid, data)
        self.assertEqual(data["filters"], {})

    def test_an_organisation_the_asker_cannot_open_reads_like_one_that_does_not_exist(self):
        danas = self.customer("Taco Bell", owner=self.other)
        globex = self.customer("Globex Corp", organisation=self.other_org)
        expected = {"filters": {"organisation": [NOT_OPEN_ORGANISATION]}}
        for pk in (danas.pk, globex.pk, 999999):
            with self.subTest(pk=pk):
                valid, errors = self.check(self.listing(organisation=f"{self.pizza.pk},{pk}"))
                self.assertFalse(valid)
                self.assertEqual(errors, expected)

    def test_the_account_and_focus_are_read_only_on_the_account_page(self):
        valid, data = self.check(
            {**self.listing(), "account": 0, "focus": {"kind": "companies", "id": "x"}}
        )

        self.assertTrue(valid, data)
        self.assertEqual(
            dict(data),
            {"surface": "accounts", "view": "list", "filters": {}, "label": "Accounts"},
        )


class DetailContextTests(AccountsContextFixture):
    def test_the_account_is_labelled_with_its_own_name(self):
        valid, data = self.check({**self.detail(self.emea), "label": "Spoofed"})

        self.assertTrue(valid, data)
        self.assertEqual(
            dict(data),
            {
                "surface": "accounts",
                "view": "detail",
                "account": self.emea.pk,
                "label": "Pizza Hut EMEA",
                "focus": None,
            },
        )

    def test_an_account_the_asker_cannot_open_reads_like_one_that_does_not_exist(self):
        carls = self.account("Carl's own", owner=self.csm)
        globex = self.customer("Globex Corp", organisation=self.other_org)
        elsewhere = self.account("Globex EU", customers=[globex])
        for user, account in (
            (self.other, carls.pk),
            (self.csm, elsewhere.pk),
            (self.csm, 999999),
            (self.csm, 0),
            (self.csm, None),
        ):
            with self.subTest(user=user.name, account=account):
                valid, errors = self.check(self.detail(account), user)
                self.assertFalse(valid)
                self.assertEqual(errors, {"account": [NOT_OPEN_ACCOUNT]})

    def test_a_missing_account_is_the_same_400(self):
        valid, errors = self.check({"surface": "accounts", "view": "detail"})

        self.assertFalse(valid)
        self.assertEqual(errors, {"account": [NOT_OPEN_ACCOUNT]})

    def test_a_focus_on_an_item_the_asker_may_read_is_kept(self):
        note = self.note(self.emea)

        valid, data = self.check(self.detail(self.emea, {"kind": "note", "id": note.pk}))

        self.assertTrue(valid, data)
        self.assertEqual(data["focus"], {"kind": "note", "id": note.pk})

    def test_a_focus_the_asker_may_not_read_here_reads_like_one_that_does_not_exist(self):
        private = self.note(self.emea, title="Dana's private note", author=self.other)
        on_apac = self.note(self.apac)
        on_pizza = self.note(self.pizza)
        future = self.note(self.emea, day=self.today + timedelta(days=3))
        other_department = self.ticket(self.emea, department=User.Function.ENGINEERING)
        for kind, pk in (
            ("note", private.pk),
            ("note", on_apac.pk),
            ("note", on_pizza.pk),
            ("note", future.pk),
            ("note", 999999),
            ("ticket", private.pk),
            ("ticket", other_department.pk),
        ):
            with self.subTest(kind=kind, pk=pk):
                valid, errors = self.check(self.detail(self.emea, {"kind": kind, "id": pk}))
                self.assertFalse(valid)
                self.assertEqual(errors, {"focus": [NOT_A_STORY_ITEM]})

    def test_a_malformed_focus_is_a_400(self):
        valid, errors = self.check(self.detail(self.emea, {"kind": "companies", "id": 1}))

        self.assertFalse(valid)
        self.assertIn("kind", errors["focus"])
