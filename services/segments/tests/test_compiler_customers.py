"""Every organisation field with every operator its type takes, against five
organisations whose values differ field by field. One query per case, all in
one test, over data built once.

Alpha, Beta and Gamma are the live book. Delta has churned and Echo is
archived, so they appear only when a rule names `churned` or `archived`."""

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from services.attributes.models import AIAttribute, AIAttributeValue
from services.customers.models import Activity, Customer, Product, Ticket
from services.customers.scoping import visible_customers
from services.fx_rates.models import FxRate
from services.segments import registry
from services.segments.compiler import compile_rules
from services.segments.tests.fixtures import SegmentFixture, rule

NAMES = ("Alpha", "Beta", "Gamma", "Delta", "Echo", "Foxtrot")


class CustomerFieldTests(SegmentFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        day = cls.today
        FxRate.objects.create(
            organisation=cls.org, currency="EUR", rate_to_org_currency=Decimal("1.1")
        )
        cls.core = Product.objects.create(organisation=cls.org, name="Core")

        def make(name, **fields):
            return Customer.objects.create(organisation=cls.org, name=name, **fields)

        cls.alpha = make(
            "Alpha", owner=cls.csm, lifecycle_stage="live", health_score=Decimal("8.0"),
            csat_score=Decimal("90"), nps_score=40, ces_percentage=Decimal("80"),
            currency="USD", arr_billed_at_account=Decimal("120000"),
            renewal_date=day + timedelta(days=20), primary_product=cls.core,
            total_contracted_seats=100, total_active_seats=80, ai_pulse_value=5,
            csm_pulse_score=4,
        )  # fmt: skip
        cls.beta = make(
            "Beta", owner=cls.other, lifecycle_stage="onboarding", health_score=Decimal("5.0"),
            csat_score=Decimal("55"), nps_score=0, ces_percentage=Decimal("40"),
            currency="EUR", arr_billed_at_account=Decimal("30000"),
            renewal_date=day - timedelta(days=5), total_contracted_seats=100,
            total_active_seats=10, ai_pulse_value=2, csm_pulse_score=2,
        )  # fmt: skip
        cls.gamma = make(
            "Gamma", owner=None, lifecycle_stage="adoption", health_score=Decimal("2.0"),
            nps_score=-20, currency="USD", arr_billed_at_account=Decimal("5000"),
        )  # fmt: skip
        Customer.objects.filter(pk=cls.gamma.pk).update(
            created_at=timezone.now() - timedelta(days=100)
        )
        make(
            "Delta", owner=cls.csm, lifecycle_stage="churn", churn_date=day - timedelta(days=10),
            health_score=Decimal("9.0"),
        )  # fmt: skip
        make("Echo", owner=cls.csm, is_archived=True, health_score=Decimal("9.0"))

        for title, department, status in (
            ("Down", "", Ticket.Status.OPEN),
            ("Pricing", "sales", Ticket.Status.OPEN),
        ):
            Ticket.objects.create(
                customer=cls.alpha, ticket_number=title, title=title, status=status,
                priority=Ticket.Priority.LOW, opened_at=day, department=department,
            )  # fmt: skip
        Ticket.objects.create(
            customer=cls.beta, ticket_number="Old", title="Old", status=Ticket.Status.RESOLVED,
            priority=Ticket.Priority.LOW, opened_at=day,
        )  # fmt: skip
        for customer, days_ago in ((cls.alpha, 3), (cls.beta, 40)):
            Activity.objects.create(
                customer=customer,
                type=Activity.ActivityType.OTHER,
                occurred_at=day - timedelta(days=days_ago),
            )

        def attribute(api_name, value_type, **fields):
            return AIAttribute.objects.create(
                organisation=cls.org, name=api_name.title(), api_name=api_name,
                prompt="?", value_type=value_type, **fields,
            )  # fmt: skip

        tier = attribute("tier", "picklist", picklist_options=["gold", "silver"])
        seats = attribute("seats", "number")
        sso = attribute("sso", "boolean")
        # Alpha's older answer is silver; the newest row is the value.
        for attr, customer, value in (
            (tier, cls.alpha, "silver"), (tier, cls.alpha, "gold"), (tier, cls.beta, "silver"),
            (seats, cls.alpha, 50), (seats, cls.beta, 5), (sso, cls.alpha, True),
            (sso, cls.beta, False),
            # Gamma's answer was given when "seats" was a text attribute.
            (seats, cls.gamma, "many"),
        ):  # fmt: skip
            AIAttributeValue.objects.create(attribute=attr, customer=customer, value=value)
        AIAttributeValue.objects.create(
            attribute=tier, customer=cls.gamma, value=None, status="insufficient"
        )

    def matched(self, rules, user=None):
        user = user or self.admin
        compiled = compile_rules(rules, "customer", user=user, today=self.today)
        records = visible_customers(user).filter(name__in=NAMES)
        return set(compiled.apply(records).values_list("name", flat=True))

    def cases(self):
        day = self.today

        def iso(days):
            return (day + timedelta(days=days)).isoformat()

        alpha_beta, all3 = {"Alpha", "Beta"}, {"Alpha", "Beta", "Gamma"}
        carl, dana = self.csm.pk, self.other.pk
        return [
            ("lifecycle_stage", "is", "live", {"Alpha"}),
            ("lifecycle_stage", "is_not", "live", {"Beta", "Gamma"}),
            ("lifecycle_stage", "in", ["live", "adoption"], {"Alpha", "Gamma"}),
            # Naming the churn stage lifts the churned default, as the
            # Organizations list's lifecycle filter does (ruling S4).
            ("lifecycle_stage", "is", "churn", {"Delta"}),
            ("lifecycle_stage", "in", ["live", "churn"], {"Alpha", "Delta"}),
            ("health_score", "gt", 6, {"Alpha"}),
            ("health_score", "lt", 6, {"Beta", "Gamma"}),
            ("health_score", "between", [4, 8], alpha_beta),
            ("health_score", "is_empty", None, set()),
            ("health_score", "is_not_empty", None, all3),
            ("health_category", "is", "good", {"Alpha"}),
            ("health_category", "is_not", "good", {"Beta", "Gamma"}),
            ("health_category", "in", ["average", "poor"], {"Beta", "Gamma"}),
            ("csat_score", "gt", 60, {"Alpha"}),
            ("csat_score", "lt", 60, {"Beta"}),
            ("csat_score", "between", [50, 95], alpha_beta),
            ("csat_score", "is_empty", None, {"Gamma"}),
            ("csat_score", "is_not_empty", None, alpha_beta),
            ("nps_score", "gt", 0, {"Alpha"}),
            ("nps_score", "lt", 0, {"Gamma"}),
            ("nps_score", "between", [-10, 10], {"Beta"}),
            ("nps_score", "is_empty", None, set()),
            ("nps_score", "is_not_empty", None, all3),
            ("nps_band", "is", "promoter", {"Alpha"}),
            ("nps_band", "is_not", "promoter", {"Beta", "Gamma"}),
            ("nps_band", "in", ["passive", "detractor"], {"Beta", "Gamma"}),
            ("ces_percentage", "gt", 50, {"Alpha"}),
            ("ces_percentage", "lt", 50, {"Beta"}),
            ("ces_percentage", "between", [30, 90], alpha_beta),
            ("ces_percentage", "is_empty", None, {"Gamma"}),
            ("ces_percentage", "is_not_empty", None, alpha_beta),
            # Beta bills 30,000 EUR = 33,000 USD: over 30,000 and not under
            # 31,000, which its raw number would be.
            ("arr", "gt", 30000, alpha_beta),
            ("arr", "lt", 31000, {"Gamma"}),
            ("arr", "between", [4000, 40000], {"Beta", "Gamma"}),
            ("arr", "is_empty", None, set()),
            ("arr", "is_not_empty", None, all3),
            # Overdue counts as within.
            ("renewal_date", "within_next", 30, alpha_beta),
            ("renewal_date", "within_last", 10, {"Beta"}),
            ("renewal_date", "gt", iso(10), {"Alpha"}),
            ("renewal_date", "lt", iso(0), {"Beta"}),
            ("renewal_date", "between", [iso(-10), iso(30)], alpha_beta),
            ("renewal_date", "is_empty", None, {"Gamma"}),
            ("renewal_date", "is_not_empty", None, alpha_beta),
            ("owner", "is", carl, {"Alpha"}),
            ("owner", "is", "unassigned", {"Gamma"}),
            ("owner", "is_not", carl, {"Beta", "Gamma"}),
            ("owner", "in", [dana, "unassigned"], {"Beta", "Gamma"}),
            ("product", "is", self.core.pk, {"Alpha"}),
            ("product", "is_not", self.core.pk, {"Beta", "Gamma"}),
            ("product", "in", [self.core.pk], {"Alpha"}),
            ("seat_use", "gt", 50, {"Alpha"}),
            ("seat_use", "lt", 50, {"Beta"}),
            ("seat_use", "between", [5, 85], alpha_beta),
            ("seat_use", "is_empty", None, {"Gamma"}),
            ("seat_use", "is_not_empty", None, alpha_beta),
            # Alice is Leadership: she reads both of Alpha's open tickets.
            ("open_tickets", "gt", 1, {"Alpha"}),
            ("open_tickets", "lt", 1, {"Beta", "Gamma"}),
            ("open_tickets", "between", [2, 2], {"Alpha"}),
            ("open_tickets", "is_empty", None, set()),
            ("open_tickets", "is_not_empty", None, all3),
            # Gamma was never touched: more days than any number.
            ("last_touch", "gt", 30, {"Beta", "Gamma"}),
            ("last_touch", "lt", 30, {"Alpha"}),
            ("last_touch", "between", [1, 5], {"Alpha"}),
            ("last_touch", "is_empty", None, {"Gamma"}),
            ("ai_pulse", "gt", 3, {"Alpha"}),
            ("ai_pulse", "lt", 3, {"Beta"}),
            ("ai_pulse", "between", [2, 5], alpha_beta),
            ("ai_pulse", "is_empty", None, {"Gamma"}),
            ("ai_pulse", "is_not_empty", None, alpha_beta),
            ("csm_pulse", "gt", 3, {"Alpha"}),
            ("csm_pulse", "lt", 3, {"Beta"}),
            ("csm_pulse", "between", [2, 4], alpha_beta),
            ("csm_pulse", "is_empty", None, {"Gamma"}),
            ("csm_pulse", "is_not_empty", None, alpha_beta),
            ("churned", "is", True, {"Delta"}),
            ("churned", "is", False, all3),
            ("archived", "is", True, {"Echo"}),
            ("archived", "is", False, all3),
            ("created", "within_next", 30, alpha_beta),
            ("created", "within_last", 30, alpha_beta),
            ("created", "gt", iso(-50), alpha_beta),
            ("created", "lt", iso(-50), {"Gamma"}),
            ("created", "between", [iso(-200), iso(-50)], {"Gamma"}),
            ("created", "is_empty", None, set()),
            ("created", "is_not_empty", None, all3),
            ("attr:tier", "is", "gold", {"Alpha"}),
            ("attr:tier", "is_not", "gold", {"Beta", "Gamma"}),
            ("attr:tier", "in", ["gold", "silver"], alpha_beta),
            ("attr:tier", "is_empty", None, {"Gamma"}),
            ("attr:tier", "is_not_empty", None, alpha_beta),
            # Gamma's "many" is not a number, and must not break the cast.
            ("attr:seats", "gt", 10, {"Alpha"}),
            ("attr:seats", "lt", 10, {"Beta"}),
            ("attr:seats", "between", [1, 100], alpha_beta),
            ("attr:seats", "is_empty", None, {"Gamma"}),
            ("attr:seats", "is_not_empty", None, alpha_beta),
            ("attr:sso", "is", True, {"Alpha"}),
            ("attr:sso", "is", False, {"Beta"}),
            ("attr:sso", "is_empty", None, {"Gamma"}),
            ("attr:sso", "is_not_empty", None, alpha_beta),
        ]

    def test_every_field_and_operator(self):
        for field, op, value, expected in self.cases():
            with self.subTest(field=field, op=op, value=value):
                self.assertEqual(self.matched(rule(field, op, value)), expected)

    def test_the_cases_cover_every_field_and_operator(self):
        covered = {(field, op) for field, op, _value, _expected in self.cases()}
        wanted = {
            (key, op) for key, field in registry.CUSTOMER_FIELDS.items() for op in field.operators
        }
        for attribute in AIAttribute.objects.filter(organisation=self.org):
            field = registry.attribute_field(attribute)
            wanted |= {(field.key, op) for op in field.operators}
        self.assertEqual(covered, wanted)

    def test_open_tickets_count_only_what_the_viewer_may_read(self):
        # Carl is Customer Success: Alpha's Sales ticket is not his to count.
        self.assertEqual(self.matched(rule("open_tickets", "between", [1, 1]), self.csm), {"Alpha"})
        self.assertEqual(self.matched(rule("open_tickets", "between", [1, 1])), set())

    def test_arr_follows_the_workspace_mapping(self):
        Customer.objects.filter(pk=self.alpha.pk).update(arr_billed_at_hq=Decimal("200000"))
        organisation = self.admin.organisation
        organisation.global_attributes = {"arr": "arr_billed_at_hq"}
        organisation.save(update_fields=["global_attributes"])
        self.assertEqual(self.matched(rule("arr", "gt", 150000)), {"Alpha"})

    def test_arr_in_a_currency_with_no_rate_never_matches(self):
        Customer.objects.create(
            organisation=self.org, name="Foxtrot", currency="JPY",
            arr_billed_at_account=Decimal("9000000"), owner=self.csm,
        )  # fmt: skip
        self.assertNotIn("Foxtrot", self.matched(rule("arr", "gt", 0)))
        self.assertEqual(self.matched(rule("arr", "is_empty")), {"Foxtrot"})

    def test_seat_use_is_rounded_to_two_decimals_as_the_page_shows_it(self):
        Customer.objects.create(
            organisation=self.org, name="Foxtrot", owner=self.csm,
            total_contracted_seats=300, total_active_seats=100,
        )  # fmt: skip
        # 100 of 300 is 33.333...%, which the page shows as 33.33.
        self.assertEqual(self.matched(rule("seat_use", "between", [33.33, 33.33])), {"Foxtrot"})

    def test_groups_and_match_any(self):
        rules = {
            "match": "any",
            "conditions": [
                {"field": "lifecycle_stage", "op": "is", "value": "live"},
                {
                    "group": {
                        "match": "all",
                        "conditions": [
                            {"field": "csat_score", "op": "is_empty"},
                            {"field": "nps_band", "op": "is", "value": "detractor"},
                        ],
                    }
                },
            ],
        }
        self.assertEqual(self.matched(rules), {"Alpha", "Gamma"})
        rules["match"] = "all"
        self.assertEqual(self.matched(rules), set())

    def test_an_empty_rule_list_matches_nothing(self):
        self.assertEqual(self.matched({"match": "all", "conditions": []}), set())

    def test_a_field_or_attribute_that_no_longer_resolves_matches_nothing(self):
        self.assertEqual(self.matched(rule("retired_field", "is", "x")), set())
        self.assertEqual(self.matched(rule("attr:deleted", "is", "x")), set())

    def test_named_and_conditions_are_reported(self):
        rules = {
            "match": "all",
            "conditions": [
                {"field": "churned", "op": "is", "value": True},
                {
                    "group": {
                        "match": "any",
                        "conditions": [
                            {"field": "csat_score", "op": "is_empty"},
                            {"field": "attr:tier", "op": "is", "value": "gold"},
                        ],
                    }
                },
            ],
        }
        compiled = compile_rules(rules, "customer", user=self.admin, today=self.today)
        self.assertEqual(compiled.named, {"churned", "csat_score", "attr:tier"})
        self.assertEqual(
            [keys for keys, _q in compiled.conditions],
            [("churned",), ("csat_score", "attr:tier")],
        )

    def test_compiling_reads_the_rates_and_attributes_once(self):
        rules = {
            "match": "all",
            "conditions": [
                {"field": "arr", "op": "gt", "value": 1},
                {"field": "arr", "op": "lt", "value": 10**9},
                {"field": "attr:tier", "op": "is", "value": "gold"},
            ],
        }
        with self.assertNumQueries(2):
            compile_rules(rules, "customer", user=self.admin, today=self.today)
