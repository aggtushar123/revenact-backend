"""The registry and the 400s: every field a rule may name, the operators its
type allows, the values each takes, and ids the writer must be able to open.
Then how a stored rule reads to someone who cannot open what it names."""

from services.attributes.models import AIAttribute
from services.customers.models import Product
from services.customers.tests.test_views import blind_to_one_account
from services.segments import registry
from services.segments.rules import NOT_OPEN, RuleError, present_rules, validate_rules
from services.segments.tests.fixtures import SegmentFixture, rule


class RuleFixture(SegmentFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.tier = AIAttribute.objects.create(
            organisation=cls.org,
            name="Tier",
            api_name="tier",
            prompt="Which tier?",
            value_type=AIAttribute.ValueType.PICKLIST,
            picklist_options=["gold", "silver"],
        )
        cls.seats = AIAttribute.objects.create(
            organisation=cls.org,
            name="Seats",
            api_name="seats",
            prompt="How many seats?",
            value_type=AIAttribute.ValueType.NUMBER,
            applies_to_account=True,
        )
        AIAttribute.objects.create(
            organisation=cls.other_org,
            name="Globex only",
            api_name="globex_only",
            prompt="?",
            value_type=AIAttribute.ValueType.TEXT,
        )
        cls.core = Product.objects.create(organisation=cls.org, name="Core")
        cls.their_product = Product.objects.create(organisation=cls.other_org, name="Theirs")

    def check(self, rules, kind="customer", user=None):
        return validate_rules(rules, kind, user=user or self.csm)

    def refused(self, rules, message, kind="customer", user=None):
        with self.assertRaisesMessage(RuleError, message):
            self.check(rules, kind, user)


class ShapeTests(RuleFixture):
    def test_an_empty_rule_list_is_valid(self):
        empty = {"match": "all", "conditions": []}
        self.assertEqual(self.check(empty), empty)

    def test_the_clean_copy_is_not_the_input(self):
        rules = rule("health_score", "gt", 5)
        self.assertIsNot(self.check(rules), rules)

    def test_match_must_be_all_or_any(self):
        self.refused({"match": "most", "conditions": []}, '"match" is "all" or "any".')

    def test_unexpected_keys_and_shapes_are_refused(self):
        self.refused({"match": "all"}, "Rules are")
        self.refused({"match": "all", "conditions": [], "extra": 1}, "Rules are")
        self.refused({"match": "all", "conditions": {}}, '"conditions" is a list.')
        self.refused({"match": "all", "conditions": ["csat"]}, "names a field and an operator")
        self.refused(
            {"match": "all", "conditions": [{"field": "csat_score", "op": "gt", "x": 1}]},
            "names a field and an operator",
        )

    def test_groups_hold_conditions_and_do_not_nest(self):
        group = {
            "group": {"match": "any", "conditions": [{"field": "csat_score", "op": "is_empty"}]}
        }
        self.check({"match": "all", "conditions": [group]})
        self.refused(
            {"match": "all", "conditions": [{"group": {"match": "any", "conditions": []}}]},
            "A group needs at least one condition.",
        )
        nested = {"group": {"match": "any", "conditions": [group]}}
        self.refused({"match": "all", "conditions": [nested]}, "Groups do not nest.")

    def test_at_most_twenty_conditions_counting_inside_groups(self):
        leaf = {"field": "csat_score", "op": "is_empty"}
        group = {"group": {"match": "any", "conditions": [leaf] * 10}}
        self.check({"match": "all", "conditions": [group, *[leaf] * 10]})
        self.refused(
            {"match": "all", "conditions": [group, *[leaf] * 11]},
            "A segment can have at most 20 conditions.",
        )


class FieldTests(RuleFixture):
    def test_unknown_fields_are_refused_per_kind(self):
        self.refused(rule("password", "is", "x"), 'Unknown field "password" for organisations.')
        self.refused(
            rule("ces_percentage", "gt", 1),
            'Unknown field "ces_percentage" for accounts.',
            "account",
        )
        self.refused(
            rule("churned", "is", True), 'Unknown field "churned" for accounts.', "account"
        )
        self.refused(rule("role", "is", "champion"), 'Unknown field "role" for organisations.')
        self.refused(
            rule("parent.health_score", "gt", 1),
            'Unknown field "parent.health_score" for organisations.',
        )

    def test_every_registry_field_accepts_exactly_its_types_operators(self):
        for kind, fields in registry.FIELDS.items():
            for key, field in fields.items():
                self.assertEqual(field.operators, registry.OPERATORS[field.type], (kind, key))
        self.assertEqual(
            set(registry.CUSTOMER_FIELDS),
            {
                "lifecycle_stage", "health_score", "health_category", "csat_score", "nps_score",
                "nps_band", "ces_percentage", "arr", "renewal_date", "owner", "product",
                "seat_use", "open_tickets", "last_touch", "ai_pulse", "csm_pulse", "churned",
                "archived", "created",
            },
        )  # fmt: skip
        self.assertEqual(
            set(registry.ACCOUNT_FIELDS),
            set(registry.CUSTOMER_FIELDS)
            - {"ces_percentage", "product", "seat_use", "churned", "archived"}
            | {"organisation"},
        )
        self.assertEqual(
            set(registry.CONTACT_FIELDS),
            {
                "role",
                "sentiment",
                "status",
                "language",
                "last_contacted",
                "organisation",
                "account",
            },
        )

    def test_an_operator_its_type_does_not_take_is_refused(self):
        self.refused(rule("health_score", "is", 5), '"is" cannot be used with Health score.')
        self.refused(rule("churned", "gt", 1), '"gt" cannot be used with Churned.')
        self.refused(
            rule("lifecycle_stage", "lt", "live"), '"lt" cannot be used with Lifecycle stage.'
        )
        self.refused(rule("owner", "between", [1, 2]), '"between" cannot be used with Owner.')
        self.refused(
            rule("csat_score", "sounds_like", 1), '"sounds_like" cannot be used with CSAT %.'
        )

    def test_values_are_checked_by_type(self):
        cases = (
            (rule("health_score", "gt", "7"), "Health score: a number."),
            (rule("health_score", "gt", True), "Health score: a number."),
            (rule("health_score", "between", [8, 2]), "must not be above the second"),
            (rule("health_score", "between", [2]), "between takes two values."),
            (rule("renewal_date", "within_next", 0), "a number of days from 1 to 3650."),
            (rule("renewal_date", "within_last", 4000), "a number of days from 1 to 3650."),
            (rule("renewal_date", "gt", "next week"), "Renewal date: a date as YYYY-MM-DD."),
            (rule("last_touch", "gt", -1), "a whole number of days."),
            (rule("lifecycle_stage", "is", "asleep"), '"asleep" is not one of its values.'),
            (rule("lifecycle_stage", "in", []), "choose from 1 to 100 values."),
            (rule("churned", "is", "yes"), "Churned: true or false."),
            (rule("owner", "is", "carl"), 'a person\'s id or "unassigned".'),
            (rule("product", "is", "core"), "Product: an id."),
            (rule("csat_score", "is_empty", 1), "CSAT % is empty takes no value."),
        )
        for rules, message in cases:
            with self.subTest(rules=rules):
                self.refused(rules, message)
        self.refused(rule("language", "is", ""), "text of up to 100 characters.", "contact")
        self.check(rule("renewal_date", "between", ["2026-01-01", "2026-12-31"]))
        self.check(rule("csat_score", "between", [50, 50.5]))

    def test_contacts_name_parent_fields(self):
        self.check(rule("parent.health_score", "gt", 6), "contact")
        self.check(rule("parent.ces_percentage", "gt", 50), "contact")
        self.check(rule("parent.attr:tier", "is", "gold"), "contact")
        self.refused(
            rule("parent.organisation", "is", self.pizza.pk),
            'Unknown field "parent.organisation" for contacts.',
            "contact",
        )
        self.refused(
            rule("parent.role", "is", "champion"), 'Unknown field "parent.role"', "contact"
        )


class AttributeTests(RuleFixture):
    def test_an_ai_attribute_is_named_by_api_name_and_typed_by_its_value_type(self):
        self.check(rule("attr:seats", "gt", 10))
        self.check(rule("attr:tier", "in", ["gold", "silver"]))
        self.check(rule("attr:tier", "is_empty"))
        self.refused(rule("attr:seats", "is", 10), '"is" cannot be used with Seats.')
        self.refused(rule("attr:tier", "is", "bronze"), '"bronze" is not one of its values.')

    def test_an_attribute_of_another_workspace_or_kind_reads_as_unknown(self):
        self.refused(rule("attr:globex_only", "is", "x"), 'Unknown field "attr:globex_only"')
        self.refused(
            rule("attr:tier", "is", "gold"), 'Unknown field "attr:tier" for accounts.', "account"
        )
        self.check(rule("attr:seats", "gt", 1), "account")

    def test_attributes_are_read_in_one_query_and_not_at_all_when_unnamed(self):
        with self.assertNumQueries(0):
            self.assertEqual(registry.attributes_for(self.org.pk, ["csat_score"]), {})
        with self.assertNumQueries(1):
            found = registry.attributes_for(self.org.pk, ["attr:tier", "parent.attr:seats"])
        self.assertEqual(set(found), {"tier", "seats"})


class IdTests(RuleFixture):
    def test_an_organisation_must_be_one_the_writer_can_open(self):
        self.check(rule("organisation", "is", self.pizza.pk), "account")
        for missing_or_hidden in (self.taco.pk, self.globex.pk, 999999):
            with self.subTest(id=missing_or_hidden):
                self.refused(
                    rule("organisation", "in", [self.pizza.pk, missing_or_hidden]),
                    NOT_OPEN["customer"],
                    "account",
                )

    def test_blind_to_one_account(self):
        viewer, seen, hidden = blind_to_one_account(self.pizza)
        self.check(rule("account", "is", seen.pk), "contact", viewer)
        self.refused(rule("account", "is", hidden.pk), NOT_OPEN["account"], "contact", viewer)
        self.refused(rule("account", "is", 999999), NOT_OPEN["account"], "contact", viewer)

    def test_people_and_products_come_from_the_writers_workspace(self):
        self.check(rule("owner", "in", [self.other.pk, "unassigned"]))
        self.refused(rule("owner", "is", self.stranger.pk), NOT_OPEN["user"])
        self.check(rule("product", "is", self.core.pk))
        self.refused(rule("product", "is", self.their_product.pk), NOT_OPEN["product"])
        self.check(rule("parent.owner", "is", self.csm.pk), "contact")

    def test_a_null_id_names_nothing_and_is_accepted(self):
        self.check(rule("organisation", "in", [None, self.pizza.pk]), "account")


class PresentTests(RuleFixture):
    def test_the_owner_reads_the_names_of_what_they_can_open(self):
        stored = rule("organisation", "in", [self.pizza.pk])
        rules, labels = present_rules(stored, "account", user=self.csm)
        self.assertEqual(rules, stored)
        self.assertEqual(labels["organisations"], {str(self.pizza.pk): "Pizza Hut"})

    def test_an_id_the_reader_cannot_open_reads_null_and_is_never_named(self):
        stored = {
            "match": "all",
            "conditions": [
                {"field": "organisation", "op": "in", "value": [self.pizza.pk, self.taco.pk]},
                {"field": "owner", "op": "is", "value": self.stranger.pk},
            ],
        }
        rules, labels = present_rules(stored, "account", user=self.other)
        self.assertEqual(rules["conditions"][0]["value"], [None, self.taco.pk])
        self.assertIsNone(rules["conditions"][1]["value"])
        self.assertEqual(labels["organisations"], {str(self.taco.pk): "Taco Bell"})
        self.assertEqual(labels["people"], {})
        self.assertEqual(stored["conditions"][0]["value"], [self.pizza.pk, self.taco.pk])

    def test_people_are_named_from_the_readers_workspace(self):
        _rules, labels = present_rules(
            rule("parent.owner", "in", [self.csm.pk, "unassigned"]), "contact", user=self.other
        )
        self.assertEqual(labels["people"], {str(self.csm.pk): "Carl CSM"})
