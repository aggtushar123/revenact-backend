"""What the client may send from Pipelines, and what is stored: the page's
kind, view and URL filters in one canonical form with a server-built label,
and at most one item asked about — each 400 reading the same whether the id
exists or not."""

from services.accounts.models import User
from services.copilot.pipelines_context import (
    NOT_OPEN_ACCOUNT,
    NOT_OPEN_ITEM,
    NOT_OPEN_ORGANISATION,
    PipelinesContextSerializer,
    params_of,
)
from services.customers.models import Customer
from services.customers.tests.test_views import blind_to_one_account
from services.pipelines_portfolio.kinds import OPPORTUNITIES, RISKS
from services.pipelines_portfolio.tests.fixtures import PipelineFixture


class PipelinesContextFixture(PipelineFixture):
    """PipelineFixture: Carl owns Pizza Hut and Dana Taco Bell; Sid is in
    Sales; Alice is the Leadership admin; Globex is another tenant."""

    def check(self, data, user=None):
        serializer = PipelinesContextSerializer(data=data, context={"user": user or self.csm})
        valid = serializer.is_valid()
        return valid, (serializer.validated_data if valid else serializer.errors)

    @staticmethod
    def listing(view="list", kind="opportunities", focus=None, **filters):
        return {
            "surface": "pipelines",
            "kind": kind,
            "view": view,
            "filters": filters,
            "focus": focus,
        }


class ContextTests(PipelinesContextFixture):
    def test_no_filters_is_the_whole_book(self):
        valid, data = self.check(self.listing())

        self.assertTrue(valid, data)
        self.assertEqual(
            dict(data),
            {
                "surface": "pipelines",
                "kind": "opportunities",
                "view": "list",
                "filters": {},
                "label": "Pipelines · Opportunities",
                "focus": None,
            },
        )

    def test_the_kind_defaults_to_opportunities_as_the_url_does(self):
        valid, data = self.check({"surface": "pipelines", "view": "board"})

        self.assertTrue(valid, data)
        self.assertEqual(
            (data["kind"], data["view"], data["label"]),
            ("opportunities", "board", "Pipelines · Opportunities"),
        )

    def test_risks_are_labelled_as_risks(self):
        valid, data = self.check(self.listing("board", "risks", date="30"))

        self.assertTrue(valid, data)
        self.assertEqual(data["label"], "Pipelines · Risks · Due within 30 days")
        self.assertEqual(data["filters"], {"date": "30"})

    def test_an_unknown_kind_or_view_is_a_400(self):
        valid, errors = self.check(self.listing(kind="deals"))
        self.assertFalse(valid)
        self.assertEqual(set(errors), {"kind"})

        valid, errors = self.check(self.listing(view="detail"))
        self.assertFalse(valid)
        self.assertEqual(set(errors), {"view"})

    def test_every_label_in_toolbar_order(self):
        emea = self.account("EMEA")

        valid, data = self.check(
            {
                **self.listing(
                    ids=[11, 12],
                    search="seat",
                    organisation=str(self.pizza.pk),
                    account=str(emea.pk),
                    owner="unassigned",
                    stage="negotiation",
                    priority="high",
                    department="cs,none",
                    date="90",
                    changed="quarter",
                    sort="date",
                    group="month",
                ),
                "label": "Spoofed",
            }
        )

        self.assertTrue(valid, data)
        self.assertEqual(
            data["label"],
            'Pipelines · Opportunities · Chosen opportunities (2) · Search: "seat" · '
            "Organisation: Pizza Hut · Account: EMEA · Owner: Unassigned · Stage: Negotiation · "
            "Priority: High · Department: Customer Success, No department · "
            "Closes within 90 days · Stage changed this quarter",
        )
        self.assertEqual(
            data["filters"],
            {
                "ids": "11,12",
                "search": "seat",
                "organisation": str(self.pizza.pk),
                "account": str(emea.pk),
                "owner": "unassigned",
                "stage": "negotiation",
                "priority": "high",
                "department": "cs,none",
                "date": "90",
                "changed": "quarter",
                "sort": "date",
                "group": "month",
            },
        )

    def test_unknown_values_and_defaults_are_dropped_not_rejected(self):
        valid, data = self.check(
            self.listing(
                stage="bogus",
                priority="urgent",
                department="hr",
                date="45",
                changed="year",
                owner="nobody",
                sort="-bogus",
                group="stage",
                cursor="abc",
                limit="5",
            )
        )

        self.assertTrue(valid, data)
        self.assertEqual(data["filters"], {})
        self.assertEqual(data["label"], "Pipelines · Opportunities")

    def test_the_open_stages_named_outright_are_the_default(self):
        valid, data = self.check(self.listing(stage=",".join(OPPORTUNITIES.open_stages)))

        self.assertTrue(valid, data)
        self.assertEqual(data["filters"], {})

    def test_on_the_board_every_stage_is_the_default_and_the_open_ones_a_filter(self):
        valid, data = self.check(self.listing("board", stage=",".join(OPPORTUNITIES.stages)))
        self.assertTrue(valid, data)
        self.assertEqual(data["filters"], {})
        self.assertEqual(data["label"], "Pipelines · Opportunities")

        valid, data = self.check(self.listing("board", stage=",".join(OPPORTUNITIES.open_stages)))
        self.assertTrue(valid, data)
        self.assertEqual(data["filters"], {"stage": ",".join(OPPORTUNITIES.open_stages)})
        self.assertEqual(
            data["label"],
            "Pipelines · Opportunities · Stage: Discovery, Qualification, Solution Validation, "
            "Proposal / Price Review, Negotiation",
        )

    def test_with_no_stage_the_list_reads_the_open_stages_and_the_board_every_stage(self):
        for kind in (OPPORTUNITIES, RISKS):
            for filters in ({}, {"stage": "bogus"}):
                with self.subTest(kind=kind.key, filters=filters):
                    self.assertEqual(params_of(filters, kind, "list").stages, kind.open_stages)
                    self.assertEqual(params_of(filters, kind, "board").stages, kind.stages)
        # A stage named outright is kept on both views.
        for view in ("list", "board"):
            with self.subTest(view=view):
                params = params_of({"stage": "closed_won"}, OPPORTUNITIES, view)
                self.assertEqual(params.stages, ("closed_won",))

    def test_not_grouped_reads_as_stage_on_the_board_as_the_page_does(self):
        self.assertEqual(params_of({"group": "none"}, OPPORTUNITIES, "board").group, "stage")
        self.assertEqual(params_of({"group": "none"}, OPPORTUNITIES, "list").group, "")
        self.assertEqual(params_of({}, OPPORTUNITIES, "list").group, "stage")
        self.assertEqual(params_of({"group": "month"}, OPPORTUNITIES, "board").group, "month")

    def test_no_grouping_is_stored_as_the_pages_none(self):
        for group in ("", "none"):
            with self.subTest(group=group):
                valid, data = self.check(self.listing(group=group))
                self.assertTrue(valid, data)
                self.assertEqual(data["filters"], {"group": "none"})

    def test_an_overlong_search_is_dropped_not_rejected(self):
        valid, data = self.check(self.listing(search="x" * 101))

        self.assertTrue(valid, data)
        self.assertEqual(data["filters"], {})

    def test_owners_are_named_only_from_the_askers_own_book(self):
        self.opportunity("Upsell")
        self.opportunity("Taco deal", customer=self.taco)
        outsider = User.objects.create_user(
            email="gus@globex.io",
            password="supersecret1",
            name="Gus Globex",
            organisation=self.other_org,
            role=User.Role.CSM,
        )
        cases = {
            str(self.csm.pk): "Owner: Carl CSM",
            str(self.other.pk): "Owner: Not in your book",
            str(outsider.pk): "Owner: Not in your book",
            "999999": "Owner: Not in your book",
            "outside": "Owner: Not in your book",
            "unassigned": "Owner: Unassigned",
        }
        for owner, label in cases.items():
            with self.subTest(owner=owner):
                valid, data = self.check(self.listing(owner=owner))
                self.assertTrue(valid, data)
                self.assertEqual(data["label"], f"Pipelines · Opportunities · {label}")
                self.assertEqual(data["filters"], {"owner": owner})

    def test_a_colleague_whose_only_items_are_in_another_department_is_not_named(self):
        # Carl opens Burger King (he owns an account under it), but Sid's
        # only item there is in Sales, which Carl may not read.
        burger = Customer.objects.create(
            organisation=self.org, name="Burger King", owner=self.sales
        )
        self.account("Burger EU", customers=[burger])
        self.opportunity("Sales only", customer=burger, department=User.Function.SALES)

        for user, label in (
            (self.csm, "Owner: Not in your book"),
            (self.admin, "Owner: Sid Sales"),
        ):
            with self.subTest(user=user.name):
                valid, data = self.check(self.listing(owner=str(self.sales.pk)), user)
                self.assertTrue(valid, data)
                self.assertEqual(data["label"], f"Pipelines · Opportunities · {label}")


class RefusalTests(PipelinesContextFixture):
    def test_an_organisation_the_asker_cannot_open_reads_like_one_that_does_not_exist(self):
        expected = {"filters": {"organisation": [NOT_OPEN_ORGANISATION]}}
        for user, pk in (
            (self.csm, self.taco.pk),
            (self.csm, self.globex.pk),
            (self.csm, 999999),
            (self.admin, self.globex.pk),
        ):
            with self.subTest(user=user.name, pk=pk):
                valid, errors = self.check(self.listing(organisation=f"{self.pizza.pk},{pk}"), user)
                self.assertFalse(valid)
                self.assertEqual(errors, expected)

    def test_an_account_the_asker_cannot_open_reads_like_one_that_does_not_exist(self):
        danas = self.account("Dana's", customers=[self.taco], owner=self.other)
        globex = self.account("Globex EU", customers=[self.globex], owner=None)
        expected = {"filters": {"account": [NOT_OPEN_ACCOUNT]}}
        for pk in (danas.pk, globex.pk, 999999):
            with self.subTest(pk=pk):
                valid, errors = self.check(self.listing(account=str(pk)))
                self.assertFalse(valid)
                self.assertEqual(errors, expected)

    def test_blind_to_one_account(self):
        viewer, seen, hidden = blind_to_one_account(self.pizza)
        on_seen = self.opportunity("On seen", account=seen)
        on_hidden = self.opportunity("On hidden", account=hidden)

        valid, data = self.check(
            self.listing(focus={"kind": "opportunity", "id": on_seen.pk}), viewer
        )
        self.assertTrue(valid, data)

        valid, errors = self.check(
            self.listing(focus={"kind": "opportunity", "id": on_hidden.pk}), viewer
        )
        self.assertEqual((valid, errors), (False, {"focus": [NOT_OPEN_ITEM]}))
        valid, errors = self.check(self.listing(account=str(hidden.pk)), viewer)
        self.assertEqual((valid, errors), (False, {"filters": {"account": [NOT_OPEN_ACCOUNT]}}))


class FocusTests(PipelinesContextFixture):
    def test_a_focus_on_an_item_the_asker_may_read_is_kept_and_needs_no_filter(self):
        won = self.opportunity("Won", stage="closed_won")

        valid, data = self.check(
            self.listing(focus={"kind": "opportunity", "id": won.pk, "label": "x"}, priority="low")
        )

        self.assertTrue(valid, data)
        self.assertEqual(data["focus"], {"kind": "opportunity", "id": won.pk})
        self.assertEqual(data["label"], "Pipelines · Opportunities · Priority: Low")

    def test_a_risk_is_asked_about_from_the_risks_book(self):
        risk = self.risk("Budget")

        valid, data = self.check(self.listing("board", "risks", {"kind": "risk", "id": risk.pk}))
        self.assertTrue(valid, data)
        self.assertEqual(data["focus"], {"kind": "risk", "id": risk.pk})

        valid, errors = self.check(self.listing("board", focus={"kind": "risk", "id": risk.pk}))
        self.assertEqual((valid, errors), (False, {"focus": [NOT_OPEN_ITEM]}))

    def test_a_focus_the_asker_may_not_read_reads_like_one_that_does_not_exist(self):
        mine = self.opportunity("Mine")
        sales = self.opportunity("Sales'", department=User.Function.SALES)
        taco = self.opportunity("Taco deal", customer=self.taco)
        globex = self.opportunity("Globex deal", customer=self.globex, department="")
        for focus in (
            {"kind": "opportunity", "id": sales.pk},
            {"kind": "opportunity", "id": taco.pk},
            {"kind": "opportunity", "id": globex.pk},
            {"kind": "opportunity", "id": 999999},
            {"kind": "risk", "id": mine.pk},
            {"kind": "opportunity", "id": str(mine.pk)},
            {"kind": "opportunity", "id": True},
            {"kind": "opportunity"},
            {"id": mine.pk},
            "opportunity",
            [mine.pk],
        ):
            with self.subTest(focus=focus):
                valid, errors = self.check(self.listing(focus=focus))
                self.assertFalse(valid)
                self.assertEqual(errors, {"focus": [NOT_OPEN_ITEM]})
        # Positive control: Leadership reads every department.
        self.assertTrue(
            self.check(self.listing(focus={"kind": "opportunity", "id": sales.pk}), self.admin)[0]
        )
