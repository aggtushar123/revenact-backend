"""The conditions a scenario can ask and the routing it can do.

Embeddings are patched where the engine imports them: vectors are chosen
by hand so "close enough" is decided by the test, not by the model."""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from services.accounts.models import Organisation, User
from services.attributes.models import AIAttribute, AIAttributeValue
from services.customers.models import Customer, Email, Ticket
from services.notifications.models import Notification
from services.scenarios.engine import run_scenario
from services.scenarios.models import Scenario, ScenarioRun
from services.scenarios.tests.test_engine import action_node, edge, entry, operator_node

EMBED = "services.scenarios.engine.embed"


def vectors(mapping):
    """An embed() that answers from a table, so similarity is exact."""

    def embed(texts):
        return [mapping[text] for text in texts]

    return embed


class Fixture(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        mk = lambda email, name, **kw: User.objects.create_user(  # noqa: E731
            email=email, password="x", name=name, organisation=self.org, **kw
        )
        self.alice = mk("alice@acme.io", "Alice", role=User.Role.ADMIN)
        self.dana = mk("dana@acme.io", "Dana", reports_to=self.alice)
        self.eve = mk("eve@acme.io", "Eve", reports_to=self.alice)
        self.pizza = Customer.objects.create(
            organisation=self.org,
            name="Pizza Hut",
            domain="pizzahut.com",
            email="ops@pizzahut.com",
            owner=self.dana,
            health_score=Decimal("4.5"),
            nps_score=20,
            arr_billed_at_account=Decimal("120000"),
            renewal_date=timezone.localdate() + timedelta(days=20),
        )

    def scenario(self, *nodes_and_edges, name="Test"):
        nodes, edges = nodes_and_edges[0], nodes_and_edges[1]
        return Scenario.objects.create(
            organisation=self.org, name=name, nodes=nodes, edges=edges, is_active=True
        )

    def branch(self, **condition):
        """A scenario that creates a task named Yes or No by its condition."""
        nodes = [
            entry(),
            operator_node("cond", "Condition", **condition),
            action_node("yes", "Create Task", taskTitle="Yes"),
            action_node("no", "Create Task", taskTitle="No"),
        ]
        edges = [
            edge("entry", "cond"),
            edge("cond", "yes", "Yes"),
            edge("cond", "no", "No"),
        ]
        return self.scenario(nodes, edges)

    def taken(self, scenario, customer=None):
        run = run_scenario(scenario, customer or self.pizza, triggered_by="manual")
        self.assertEqual(run.status, ScenarioRun.Status.SUCCESS, run.log)
        return (customer or self.pizza).tasks.order_by("-id").first().title


class CrmConditions(Fixture):
    def test_arr_reads_the_organisations_own_mapping(self):
        scenario = self.branch(
            conditionAttribute="arr", conditionOperator="greater_than", conditionValue="100000"
        )
        self.assertEqual(self.taken(scenario), "Yes")
        self.org.global_attributes = {"arr": "arr_billed_at_hq"}
        self.org.save(update_fields=["global_attributes"])
        self.assertEqual(self.taken(scenario), "No")  # nothing billed at HQ

    def test_days_until_renewal(self):
        scenario = self.branch(
            conditionAttribute="renewal_days", conditionOperator="less_than", conditionValue="30"
        )
        self.assertEqual(self.taken(scenario), "Yes")
        self.pizza.renewal_date = timezone.localdate() + timedelta(days=90)
        self.pizza.save(update_fields=["renewal_date"])
        self.assertEqual(self.taken(scenario), "No")

    def test_owner_and_is_empty(self):
        scenario = self.branch(
            conditionAttribute="owner", conditionOperator="equals", conditionValue="dana@acme.io"
        )
        self.assertEqual(self.taken(scenario), "Yes")
        unowned = self.branch(conditionAttribute="owner", conditionOperator="is_empty")
        self.assertEqual(self.taken(unowned), "No")
        self.pizza.owner = None
        self.pizza.save(update_fields=["owner"])
        self.assertEqual(self.taken(unowned), "Yes")

    def test_open_tickets(self):
        scenario = self.branch(
            conditionAttribute="open_tickets", conditionOperator="greater_than", conditionValue="1"
        )
        self.assertEqual(self.taken(scenario), "No")
        for n in range(2):
            Ticket.objects.create(
                customer=self.pizza,
                ticket_number=f"T-{n}",
                title="Broken",
                priority=Ticket.Priority.MEDIUM,
                opened_at=timezone.localdate(),
            )
        self.assertEqual(self.taken(scenario), "Yes")

    def test_is_one_of_and_contains(self):
        one_of = self.branch(
            conditionAttribute="lifecycle_stage",
            conditionOperator="is_one_of",
            conditionValue="live, onboarding",
        )
        self.pizza.lifecycle_stage = Customer.LifecycleStage.ONBOARDING
        self.pizza.save(update_fields=["lifecycle_stage"])
        self.assertEqual(self.taken(one_of), "Yes")
        self.pizza.lifecycle_stage = Customer.LifecycleStage.CHURN
        self.pizza.save(update_fields=["lifecycle_stage"])
        self.assertEqual(self.taken(one_of), "No")
        contains = self.branch(
            conditionAttribute="name", conditionOperator="contains", conditionValue="pizza"
        )
        self.assertEqual(self.taken(contains), "Yes")

    def test_an_ai_attribute_by_name(self):
        tier = AIAttribute.objects.create(
            organisation=self.org,
            name="Product tier",
            api_name="product_tier",
            prompt="Which tier?",
            value_type=AIAttribute.ValueType.PICKLIST,
            picklist_options=["SMB", "Enterprise"],
        )
        scenario = self.branch(
            conditionAttribute="attr:product_tier",
            conditionOperator="equals",
            conditionValue="Enterprise",
        )
        self.assertEqual(self.taken(scenario), "No")  # no answer yet
        AIAttributeValue.objects.create(attribute=tier, customer=self.pizza, value="Enterprise")
        self.assertEqual(self.taken(scenario), "Yes")
        # A person's correction is the one that counts.
        AIAttributeValue.objects.create(
            attribute=tier,
            customer=self.pizza,
            value="SMB",
            origin=AIAttributeValue.Origin.HUMAN,
            set_by=self.dana,
        )
        self.assertEqual(self.taken(scenario), "No")

    def test_an_unknown_attribute_is_false_not_a_crash(self):
        scenario = self.branch(
            conditionAttribute="attr:nothing", conditionOperator="equals", conditionValue="x"
        )
        self.assertEqual(self.taken(scenario), "No")


class SemanticConditions(Fixture):
    def setUp(self):
        super().setUp()
        self.email = Email.objects.create(
            customer=self.pizza,
            subject="Pricing",
            body="What would the enterprise plan cost us?",
            sent_at=timezone.now(),
        )
        self.table = {
            "asking about pricing": [1.0, 0.0],
            "unhappy with support": [0.0, 1.0],
            "Pricing. What would the enterprise plan cost us?": [0.95, 0.31],
        }

    def test_a_phrase_that_matches_recent_records_passes_and_says_what_matched(self):
        scenario = self.branch(conditionKind="semantic", conditionPhrase="asking about pricing")
        with patch(EMBED, side_effect=vectors(self.table)):
            run = run_scenario(scenario, self.pizza, triggered_by="manual")
        self.assertEqual(self.pizza.tasks.order_by("-id").first().title, "Yes")
        detail = next(row["detail"] for row in run.log if row["action"] == "Condition")
        self.assertIn("an email from", detail)
        self.assertIn("0.9", detail)
        # A run's history is filtered by customer, not by record, so the
        # log identifies what matched without quoting any of it.
        self.assertNotIn("Pricing", detail)
        self.assertNotIn("enterprise plan cost", detail)

    def test_a_phrase_that_matches_nothing_fails(self):
        scenario = self.branch(conditionKind="semantic", conditionPhrase="unhappy with support")
        with patch(EMBED, side_effect=vectors(self.table)):
            run_scenario(scenario, self.pizza, triggered_by="manual")
        self.assertEqual(self.pizza.tasks.order_by("-id").first().title, "No")

    def test_the_threshold_is_configurable(self):
        scenario = self.branch(
            conditionKind="semantic",
            conditionPhrase="asking about pricing",
            conditionThreshold="0.99",
        )
        with patch(EMBED, side_effect=vectors(self.table)):
            run_scenario(scenario, self.pizza, triggered_by="manual")
        self.assertEqual(self.pizza.tasks.order_by("-id").first().title, "No")

    def test_a_company_with_nothing_on_record_is_false_and_costs_nothing(self):
        quiet = Customer.objects.create(
            organisation=self.org, name="Quiet Co", domain="quiet.co", owner=self.dana
        )
        scenario = self.branch(conditionKind="semantic", conditionPhrase="asking about pricing")
        with patch(EMBED, side_effect=vectors(self.table)) as embed:
            run_scenario(scenario, quiet, triggered_by="manual")
        self.assertEqual(quiet.tasks.order_by("-id").first().title, "No")
        embed.assert_not_called()

    def test_an_empty_phrase_is_false(self):
        scenario = self.branch(conditionKind="semantic", conditionPhrase="  ")
        with patch(EMBED, side_effect=vectors(self.table)) as embed:
            run_scenario(scenario, self.pizza, triggered_by="manual")
        self.assertEqual(self.pizza.tasks.order_by("-id").first().title, "No")
        embed.assert_not_called()


class SemanticScope(Fixture):
    """A scenario matches on what the account's owner may read, never on
    another person's mailbox or another department's queue."""

    def test_a_colleagues_mail_and_another_departments_ticket_are_not_matched(self):
        self.dana.function = User.Function.CS
        self.dana.save(update_fields=["function"])
        Email.objects.create(
            customer=self.pizza,
            subject="Pricing",
            body="What would the enterprise plan cost us?",
            sent_at=timezone.now(),
            mailbox_owner=self.eve,
        )
        Ticket.objects.create(
            customer=self.pizza,
            ticket_number="T-9",
            title="Pricing of the enterprise plan",
            priority=Ticket.Priority.MEDIUM,
            opened_at=timezone.localdate(),
            department=User.Function.ENGINEERING,
        )
        table = {"asking about pricing": [1.0, 0.0]}
        scenario = self.branch(conditionKind="semantic", conditionPhrase="asking about pricing")
        with patch(EMBED, side_effect=vectors(table)) as embed:
            run_scenario(scenario, self.pizza, triggered_by="manual")
        self.assertEqual(self.pizza.tasks.order_by("-id").first().title, "No")
        # Nothing Dana may read, so nothing to embed beyond the phrase.
        embed.assert_not_called()

    def test_an_unowned_company_reads_only_what_nobody_owns(self):
        self.pizza.owner = None
        self.pizza.save(update_fields=["owner"])
        Email.objects.create(
            customer=self.pizza,
            subject="Pricing",
            body="What would the enterprise plan cost us?",
            sent_at=timezone.now(),
            mailbox_owner=self.dana,
        )
        shared = Email.objects.create(
            customer=self.pizza,
            subject="Pricing again",
            body="Still waiting on a quote.",
            sent_at=timezone.now(),
        )
        table = {
            "asking about pricing": [1.0, 0.0],
            "Pricing again. Still waiting on a quote.": [0.97, 0.24],
        }
        scenario = self.branch(conditionKind="semantic", conditionPhrase="asking about pricing")
        with patch(EMBED, side_effect=vectors(table)):
            run_scenario(scenario, self.pizza, triggered_by="manual")
        self.assertEqual(self.pizza.tasks.order_by("-id").first().title, "Yes")
        self.assertTrue(shared.id)


class Routing(Fixture):
    def test_assign_owner_to_a_named_person_and_notify_them(self):
        nodes = [
            entry(),
            action_node("assign", "Assign Owner", assignTo=self.eve.id),
            action_node("tell", "Notify", notifyWho="owner", notifyMessage="You own this now."),
        ]
        edges = [edge("entry", "assign"), edge("assign", "tell")]
        run = run_scenario(self.scenario(nodes, edges), self.pizza, triggered_by="manual")
        self.assertEqual(run.status, ScenarioRun.Status.SUCCESS, run.log)
        self.pizza.refresh_from_db()
        self.assertEqual(self.pizza.owner, self.eve)
        # Two notifications reach Eve: the assignment itself, then the
        # Notify node, which now finds her as the owner.
        messages = list(
            Notification.objects.filter(recipient=self.eve).values_list("message", flat=True)
        )
        self.assertEqual(len(messages), 2)
        self.assertTrue(any("You own this now." in message for message in messages))
        self.assertTrue(any("assigned to you" in message for message in messages))
        self.assertIn(
            str(self.pizza.id), Notification.objects.filter(recipient=self.eve).first().link
        )
        self.assertIn("Eve", run.log[0]["detail"])

    def test_assign_to_the_least_loaded_member_of_a_function(self):
        # Alice runs the place rather than a book, so she is not in the pool.
        self.alice.function = User.Function.LEADERSHIP
        self.alice.save(update_fields=["function"])
        self.dana.function = User.Function.CS
        self.dana.save(update_fields=["function"])
        self.eve.function = User.Function.CS
        self.eve.save(update_fields=["function"])
        for n in range(3):
            Customer.objects.create(
                organisation=self.org, name=f"Other {n}", domain=f"o{n}.com", owner=self.dana
            )
        nodes = [
            entry(),
            action_node("assign", "Assign Owner", assignRule="least_loaded", assignFunction="cs"),
        ]
        run = run_scenario(
            self.scenario(nodes, [edge("entry", "assign")]), self.pizza, triggered_by="manual"
        )
        self.pizza.refresh_from_db()
        self.assertEqual(self.pizza.owner, self.eve)
        self.assertEqual(run.status, ScenarioRun.Status.SUCCESS, run.log)

    def test_notify_the_managers_chain_and_a_named_person(self):
        nodes = [
            entry(),
            action_node("m", "Notify", notifyWho="manager", notifyMessage="Look at this."),
            action_node(
                "u", "Notify", notifyWho="user", notifyUser=self.eve.id, notifyMessage="You too."
            ),
        ]
        run = run_scenario(
            self.scenario(nodes, [edge("entry", "m"), edge("m", "u")]),
            self.pizza,
            triggered_by="manual",
        )
        self.assertEqual(run.status, ScenarioRun.Status.SUCCESS, run.log)
        self.assertEqual(
            Notification.objects.get(recipient=self.alice).message.count("Look at this."), 1
        )
        self.assertTrue(Notification.objects.filter(recipient=self.eve).exists())

    def test_a_routing_action_that_names_nobody_fails_that_node_only(self):
        nodes = [
            entry(),
            action_node("assign", "Assign Owner"),
            action_node("task", "Create Task", taskTitle="Still ran"),
        ]
        run = run_scenario(
            self.scenario(nodes, [edge("entry", "assign"), edge("assign", "task")]),
            self.pizza,
            triggered_by="manual",
        )
        self.assertEqual(run.status, ScenarioRun.Status.FAILED)
        self.assertEqual(self.pizza.tasks.first().title, "Still ran")

    def test_a_person_from_another_tenant_is_never_assigned(self):
        other = Organisation.objects.create(name="Other")
        outsider = User.objects.create_user(
            email="o@other.io", password="x", name="O", organisation=other
        )
        nodes = [entry(), action_node("assign", "Assign Owner", assignTo=outsider.id)]
        run = run_scenario(
            self.scenario(nodes, [edge("entry", "assign")]), self.pizza, triggered_by="manual"
        )
        self.assertEqual(run.status, ScenarioRun.Status.FAILED)
        self.pizza.refresh_from_db()
        self.assertEqual(self.pizza.owner, self.dana)


class ClassifiedTrigger(Fixture):
    """A scenario can start when an interaction is classified: the ticket
    arrives, the classifier tags it, the scenario routes it."""

    def setUp(self):
        super().setUp()
        self.scenario_row = self.scenario(
            [
                entry("interaction_classified"),
                action_node("assign", "Assign Owner", assignTo=self.eve.id),
            ],
            [edge("entry", "assign")],
        )

    def test_classifying_a_ticket_runs_the_scenario_for_its_company(self):
        from services.customers.classification import apply_classification

        ticket = Ticket.objects.create(
            customer=self.pizza,
            ticket_number="T-1",
            title="Pricing question",
            priority=Ticket.Priority.MEDIUM,
            opened_at=timezone.localdate(),
        )
        with self.captureOnCommitCallbacks(execute=True):
            apply_classification(ticket, {"ai_category": "feature_request"})
        self.pizza.refresh_from_db()
        self.assertEqual(self.pizza.owner, self.eve)
        run = ScenarioRun.objects.get(scenario=self.scenario_row)
        self.assertEqual(run.triggered_by, ScenarioRun.TriggeredBy.EVENT)

    def test_an_inactive_scenario_and_another_tenant_are_left_alone(self):
        from services.customers.classification import apply_classification

        self.scenario_row.is_active = False
        self.scenario_row.save(update_fields=["is_active"])
        email = Email.objects.create(
            customer=self.pizza, subject="Hi", body="x", sent_at=timezone.now()
        )
        with self.captureOnCommitCallbacks(execute=True):
            apply_classification(email, {"ai_category": "feature_request"})
        self.assertFalse(ScenarioRun.objects.exists())
        self.pizza.refresh_from_db()
        self.assertEqual(self.pizza.owner, self.dana)

    def test_an_account_level_record_is_ignored(self):
        from services.customers.classification import apply_classification
        from services.customers.models import Account

        account = Account.objects.create(name="APAC", domain="apac.pizzahut.com")
        account.customers.add(self.pizza)
        theirs = Email.objects.create(
            account=account, subject="Hi", body="x", sent_at=timezone.now()
        )
        with self.captureOnCommitCallbacks(execute=True):
            apply_classification(theirs, {"ai_category": "feature_request"})
        # Accounts have no engine path yet, so nothing runs rather than
        # something running against the wrong company.
        self.assertFalse(ScenarioRun.objects.exists())
