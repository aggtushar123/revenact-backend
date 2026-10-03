"""Every account field and every contact field with every operator its type
takes, plus contacts' `parent.` fields on both kinds of parent. Then the
pickers: an organisation or account id the viewer cannot open names nothing
in their book."""

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from services.attributes.models import AIAttribute, AIAttributeValue
from services.customers.models import Account, Activity, Contact, Customer, Ticket
from services.customers.scoping import visible_accounts, visible_children_q
from services.segments import registry
from services.segments.compiler import compile_rules
from services.segments.tests.fixtures import SegmentFixture, rule

ACCOUNTS = ("North", "South", "Lone")
PEOPLE = ("Sam", "Uma", "Tom", "Wes")


class AccountsAndContactsFixture(SegmentFixture):
    """North is under Pizza Hut (Carl's), South under Taco Bell (Dana's), and
    Lone is unowned and linked to both. Sam is a Pizza Hut contact, Uma a
    Pizza EMEA one, Tom a Taco Bell one and Wes a Taco West one."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        day, now = cls.today, timezone.now()
        cls.north = cls.make_account(
            "North", cls.pizza, cls.csm, lifecycle_stage="live", health_score=Decimal("8.0"),
            csat_score=Decimal("90"), nps_score=40, arr=Decimal("50000"),
            renewal_date=day + timedelta(days=20), ai_pulse_value=5, csm_pulse_score=4,
        )  # fmt: skip
        cls.south = cls.make_account(
            "South", cls.taco, cls.other, lifecycle_stage="onboarding",
            health_score=Decimal("5.0"), csat_score=Decimal("55"), nps_score=0,
            arr=Decimal("10000"), renewal_date=day - timedelta(days=5), ai_pulse_value=2,
            csm_pulse_score=2,
        )  # fmt: skip
        cls.lone = cls.make_account(
            "Lone", cls.pizza, None, lifecycle_stage="adoption", health_score=Decimal("2.0"),
            nps_score=-20, arr=Decimal("0"),
        )  # fmt: skip
        cls.lone.customers.add(cls.taco)
        Account.objects.filter(pk=cls.lone.pk).update(created_at=now - timedelta(days=100))
        Ticket.objects.create(
            account=cls.north, ticket_number="T-1", title="Down", status=Ticket.Status.OPEN,
            priority=Ticket.Priority.LOW, opened_at=day,
        )  # fmt: skip
        for account, days_ago in ((cls.north, 3), (cls.south, 40)):
            Activity.objects.create(
                account=account,
                type=Activity.ActivityType.OTHER,
                occurred_at=day - timedelta(days=days_ago),
            )
        plan = AIAttribute.objects.create(
            organisation=cls.org, name="Plan", api_name="plan", prompt="?",
            value_type="picklist", picklist_options=["pro", "free"], applies_to_account=True,
        )  # fmt: skip
        for parent, value in (
            ({"account": cls.north}, "pro"),
            ({"account": cls.south}, "free"),
            ({"customer": cls.pizza}, "pro"),
            ({"account": cls.west}, "pro"),
        ):
            AIAttributeValue.objects.create(attribute=plan, value=value, **parent)

        Customer.objects.filter(pk=cls.pizza.pk).update(ces_percentage=Decimal("80"))
        Customer.objects.filter(pk=cls.taco.pk).update(renewal_date=day + timedelta(days=10))
        Account.objects.filter(pk=cls.emea.pk).update(health_score=Decimal("9.0"))
        Account.objects.filter(pk=cls.west.pk).update(health_score=Decimal("2.0"))

        def contact(name, **fields):
            return Contact.objects.create(name=name, email=f"{name.lower()}@example.com", **fields)

        contact(
            "Sam", customer=cls.pizza, role="champion", sentiment="positive", language="fr",
            last_contacted_at=now - timedelta(days=2),
        )  # fmt: skip
        contact(
            "Uma", account=cls.emea, role="economic_buyer", sentiment="negative",
            status="inactive",
        )  # fmt: skip
        contact(
            "Tom", customer=cls.taco, role="other", sentiment="neutral", language="en",
            last_contacted_at=now - timedelta(days=40),
        )  # fmt: skip
        contact("Wes", account=cls.west, role="champion", sentiment="neutral")
        # Outside PEOPLE: only the picker test reads her. Carl may open Lone,
        # and Lone is linked to Taco Bell, which he may not.
        contact("Lia", account=cls.lone, role="other", sentiment="neutral")

    def accounts(self, rules, user=None):
        user = user or self.admin
        compiled = compile_rules(rules, "account", user=user, today=self.today)
        records = visible_accounts(user).filter(name__in=ACCOUNTS)
        return set(compiled.apply(records).values_list("name", flat=True))

    def contacts(self, rules, user=None, names=PEOPLE):
        user = user or self.admin
        compiled = compile_rules(rules, "contact", user=user, today=self.today)
        records = Contact.objects.filter(visible_children_q(user), name__in=names)
        return set(compiled.apply(records).values_list("name", flat=True))

    def iso(self, days):
        return (self.today + timedelta(days=days)).isoformat()


class AccountFieldTests(AccountsAndContactsFixture):
    def cases(self):
        ns, all3 = {"North", "South"}, set(ACCOUNTS)
        sl = {"South", "Lone"}
        carl, dana, pizza, taco = self.csm.pk, self.other.pk, self.pizza.pk, self.taco.pk
        return [
            ("lifecycle_stage", "is", "live", {"North"}),
            ("lifecycle_stage", "is_not", "live", sl),
            ("lifecycle_stage", "in", ["live", "adoption"], {"North", "Lone"}),
            ("health_score", "gt", 6, {"North"}),
            ("health_score", "lt", 6, sl),
            ("health_score", "between", [4, 8], ns),
            ("health_score", "is_empty", None, set()),
            ("health_score", "is_not_empty", None, all3),
            ("health_category", "is", "good", {"North"}),
            ("health_category", "is_not", "good", sl),
            ("health_category", "in", ["average", "poor"], sl),
            ("csat_score", "gt", 60, {"North"}),
            ("csat_score", "lt", 60, {"South"}),
            ("csat_score", "between", [50, 95], ns),
            ("csat_score", "is_empty", None, {"Lone"}),
            ("csat_score", "is_not_empty", None, ns),
            ("nps_score", "gt", 0, {"North"}),
            ("nps_score", "lt", 0, {"Lone"}),
            ("nps_score", "between", [-10, 10], {"South"}),
            ("nps_score", "is_empty", None, set()),
            ("nps_score", "is_not_empty", None, all3),
            ("nps_band", "is", "promoter", {"North"}),
            ("nps_band", "is_not", "promoter", sl),
            ("nps_band", "in", ["passive", "detractor"], sl),
            ("arr", "gt", 20000, {"North"}),
            ("arr", "lt", 20000, sl),
            ("arr", "between", [5000, 60000], ns),
            ("arr", "is_empty", None, set()),
            ("arr", "is_not_empty", None, all3),
            ("renewal_date", "within_next", 30, ns),
            ("renewal_date", "within_last", 10, {"South"}),
            ("renewal_date", "gt", self.iso(10), {"North"}),
            ("renewal_date", "lt", self.iso(0), {"South"}),
            ("renewal_date", "between", [self.iso(-10), self.iso(30)], ns),
            ("renewal_date", "is_empty", None, {"Lone"}),
            ("renewal_date", "is_not_empty", None, ns),
            ("owner", "is", carl, {"North"}),
            ("owner", "is", "unassigned", {"Lone"}),
            ("owner", "is_not", carl, sl),
            ("owner", "in", [dana, "unassigned"], sl),
            ("open_tickets", "gt", 0, {"North"}),
            ("open_tickets", "lt", 1, sl),
            ("open_tickets", "between", [1, 1], {"North"}),
            ("open_tickets", "is_empty", None, set()),
            ("open_tickets", "is_not_empty", None, all3),
            ("last_touch", "gt", 30, sl),
            ("last_touch", "lt", 30, {"North"}),
            ("last_touch", "between", [1, 5], {"North"}),
            ("last_touch", "is_empty", None, {"Lone"}),
            ("ai_pulse", "gt", 3, {"North"}),
            ("ai_pulse", "lt", 3, {"South"}),
            ("ai_pulse", "between", [2, 5], ns),
            ("ai_pulse", "is_empty", None, {"Lone"}),
            ("ai_pulse", "is_not_empty", None, ns),
            ("csm_pulse", "gt", 3, {"North"}),
            ("csm_pulse", "lt", 3, {"South"}),
            ("csm_pulse", "between", [2, 4], ns),
            ("csm_pulse", "is_empty", None, {"Lone"}),
            ("csm_pulse", "is_not_empty", None, ns),
            ("created", "within_next", 30, ns),
            ("created", "within_last", 30, ns),
            ("created", "gt", self.iso(-50), ns),
            ("created", "lt", self.iso(-50), {"Lone"}),
            ("created", "between", [self.iso(-200), self.iso(-50)], {"Lone"}),
            ("created", "is_empty", None, set()),
            ("created", "is_not_empty", None, all3),
            ("organisation", "is", pizza, {"North", "Lone"}),
            ("organisation", "is_not", pizza, {"South"}),
            ("organisation", "in", [taco], sl),
            ("attr:plan", "is", "pro", {"North"}),
            ("attr:plan", "is_not", "pro", sl),
            ("attr:plan", "in", ["pro", "free"], ns),
            ("attr:plan", "is_empty", None, {"Lone"}),
            ("attr:plan", "is_not_empty", None, ns),
        ]

    def test_every_field_and_operator(self):
        for field, op, value, expected in self.cases():
            with self.subTest(field=field, op=op, value=value):
                self.assertEqual(self.accounts(rule(field, op, value)), expected)

    def test_the_cases_cover_every_field_and_operator(self):
        covered = {(field, op) for field, op, _value, _expected in self.cases()}
        wanted = {
            (key, op) for key, field in registry.ACCOUNT_FIELDS.items() for op in field.operators
        }
        plan = registry.attribute_field(AIAttribute.objects.get(api_name="plan"))
        wanted |= {(plan.key, op) for op in plan.operators}
        self.assertEqual(covered, wanted)

    def test_an_organisation_the_viewer_cannot_open_names_nothing(self):
        # Lone is linked to Taco Bell, which Carl cannot open: the picker
        # must not tell him so.
        self.assertEqual(self.accounts(rule("organisation", "in", [self.taco.pk]), self.csm), set())
        self.assertEqual(
            self.accounts(rule("organisation", "is", self.pizza.pk), self.csm), {"North", "Lone"}
        )


class ContactFieldTests(AccountsAndContactsFixture):
    def cases(self):
        pizza, taco, emea, west = self.pizza.pk, self.taco.pk, self.emea.pk, self.west.pk
        return [
            ("role", "is", "champion", {"Sam", "Wes"}),
            ("role", "is_not", "champion", {"Uma", "Tom"}),
            ("role", "in", ["economic_buyer", "other"], {"Uma", "Tom"}),
            ("sentiment", "is", "negative", {"Uma"}),
            ("sentiment", "is_not", "neutral", {"Sam", "Uma"}),
            ("sentiment", "in", ["positive", "negative"], {"Sam", "Uma"}),
            ("status", "is", "inactive", {"Uma"}),
            ("status", "is_not", "inactive", {"Sam", "Tom", "Wes"}),
            ("status", "in", ["active"], {"Sam", "Tom", "Wes"}),
            ("language", "is", "fr", {"Sam"}),
            ("language", "is_not", "fr", {"Uma", "Tom", "Wes"}),
            ("language", "in", ["fr", "en"], {"Sam", "Tom"}),
            ("language", "is_empty", None, {"Uma", "Wes"}),
            ("language", "is_not_empty", None, {"Sam", "Tom"}),
            ("last_contacted", "gt", 30, {"Uma", "Tom", "Wes"}),
            ("last_contacted", "lt", 30, {"Sam"}),
            ("last_contacted", "between", [1, 5], {"Sam"}),
            ("last_contacted", "is_empty", None, {"Uma", "Wes"}),
            ("organisation", "is", pizza, {"Sam", "Uma"}),
            ("organisation", "is_not", pizza, {"Tom", "Wes"}),
            ("organisation", "in", [taco], {"Tom", "Wes"}),
            ("account", "is", emea, {"Uma"}),
            ("account", "is_not", emea, {"Sam", "Tom", "Wes"}),
            ("account", "in", [west], {"Wes"}),
        ]

    def test_every_field_and_operator(self):
        for field, op, value, expected in self.cases():
            with self.subTest(field=field, op=op, value=value):
                self.assertEqual(self.contacts(rule(field, op, value)), expected)

    def test_the_cases_cover_every_field_and_operator(self):
        covered = {(field, op) for field, op, _value, _expected in self.cases()}
        wanted = {
            (key, op) for key, field in registry.CONTACT_FIELDS.items() for op in field.operators
        }
        self.assertEqual(covered, wanted)

    def test_parent_fields_read_the_contacts_own_organisation_or_account(self):
        """`parent.` compiles each field with the organisation and account
        code the tests above cover field by field. These cases pin the
        mechanism: both parents, a field only an organisation has, the
        owner, an AI attribute, a date window and the churn flag."""
        cases = (
            ("parent.health_score", "gt", 6, {"Sam", "Uma"}),
            ("parent.ces_percentage", "gt", 50, {"Sam"}),
            # Decision 4: the account lacks the field, so not even is_empty.
            ("parent.ces_percentage", "is_empty", None, {"Tom"}),
            ("parent.owner", "is", self.csm.pk, {"Sam", "Uma"}),
            ("parent.attr:plan", "is", "pro", {"Sam", "Wes"}),
            ("parent.renewal_date", "within_next", 30, {"Tom"}),
            ("parent.churned", "is", False, {"Sam", "Tom"}),
        )
        for field, op, value, expected in cases:
            with self.subTest(field=field, op=op):
                self.assertEqual(self.contacts(rule(field, op, value)), expected)

    def test_a_parent_field_that_does_not_resolve_matches_nothing(self):
        self.assertEqual(self.contacts(rule("parent.organisation", "is", self.pizza.pk)), set())
        self.assertEqual(self.contacts(rule("parent.nonsense", "is", "x")), set())

    def test_ids_the_viewer_cannot_open_name_nothing(self):
        everyone = (*PEOPLE, "Lia")
        # Lia is Carl's to see, through Lone; Lone's link to Taco Bell is not
        # his, so the picker must not tell him so.
        self.assertEqual(
            self.contacts(rule("organisation", "is", self.taco.pk), self.csm, everyone), set()
        )
        self.assertEqual(
            self.contacts(rule("organisation", "is", self.taco.pk), names=everyone),
            {"Tom", "Wes", "Lia"},
        )
        self.assertEqual(self.contacts(rule("account", "is", self.west.pk), self.csm), set())
        self.assertEqual(
            self.contacts(rule("organisation", "in", [self.pizza.pk]), self.csm), {"Sam", "Uma"}
        )
