"""Unit tier: engine.run_scenario against hand-built node/edge graphs —
the exact JSON shape React Flow saves (see Scenario model's own
docstring): nodes as `{id, type, data}`, edges as `{id, source, target,
label}` with `label` the manually-set 'Yes'/'No' CustomEdge.tsx itself
uses for Condition branching (see engine.py's own docstring)."""

from django.core import mail
from django.test import TestCase

from services.accounts.models import Organisation
from services.customers.models import Customer, Task
from services.scenarios.engine import run_scenario
from services.scenarios.models import Scenario, ScenarioRun


def entry(event_trigger=None):
    return {
        "id": "entry",
        "type": "entry",
        "data": {"action": "Run Now", "label": "Start", "eventTrigger": event_trigger},
    }


def action_node(node_id, action, **data):
    return {"id": node_id, "type": "action", "data": {"action": action, "label": action, **data}}


def operator_node(node_id, action, **data):
    return {"id": node_id, "type": "operator", "data": {"action": action, "label": action, **data}}


def edge(source, target, label=None):
    e = {"id": f"{source}-{target}", "source": source, "target": target}
    if label:
        e["label"] = label
    return e


class RunScenarioTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.customer = Customer.objects.create(
            organisation=self.org,
            name="Globex",
            email="ops@globex.io",
            health_score=3,
        )

    def _scenario(self, nodes, edges):
        return Scenario.objects.create(organisation=self.org, nodes=nodes, edges=edges)

    def test_no_entry_node_fails_cleanly(self):
        scenario = self._scenario([], [])
        run = run_scenario(scenario, self.customer, ScenarioRun.TriggeredBy.MANUAL)
        self.assertEqual(run.status, ScenarioRun.Status.FAILED)
        self.assertIn("No entry node", run.log[0]["detail"])

    def test_send_email_action_really_sends(self):
        nodes = [entry(), action_node("n1", "Send Email", emailSubject="Hi", emailBody="Body")]
        edges = [edge("entry", "n1")]
        run = run_scenario(
            self._scenario(nodes, edges), self.customer, ScenarioRun.TriggeredBy.MANUAL
        )

        self.assertEqual(run.status, ScenarioRun.Status.SUCCESS)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["ops@globex.io"])
        self.assertEqual(mail.outbox[0].subject, "Hi")

    def test_send_email_without_recipient_logs_failure_not_crash(self):
        self.customer.email = ""
        self.customer.save()
        nodes = [entry(), action_node("n1", "Send Email", emailSubject="Hi", emailBody="Body")]
        run = run_scenario(
            self._scenario(nodes, [edge("entry", "n1")]),
            self.customer,
            ScenarioRun.TriggeredBy.MANUAL,
        )

        self.assertEqual(run.status, ScenarioRun.Status.FAILED)
        self.assertEqual(run.log[0]["status"], "failed")
        self.assertEqual(len(mail.outbox), 0)

    def test_create_task_creates_a_real_task(self):
        nodes = [entry(), action_node("n1", "Create Task", taskTitle="Check in")]
        run = run_scenario(
            self._scenario(nodes, [edge("entry", "n1")]),
            self.customer,
            ScenarioRun.TriggeredBy.MANUAL,
        )

        self.assertEqual(run.status, ScenarioRun.Status.SUCCESS)
        task = Task.objects.get(customer=self.customer)
        self.assertEqual(task.title, "Check in")

    def test_churn_entity_sets_lifecycle_stage_and_churn_date(self):
        nodes = [entry(), action_node("n1", "Churn Entity")]
        run_scenario(
            self._scenario(nodes, [edge("entry", "n1")]),
            self.customer,
            ScenarioRun.TriggeredBy.MANUAL,
        )

        self.customer.refresh_from_db()
        self.assertEqual(self.customer.lifecycle_stage, Customer.LifecycleStage.CHURN)
        self.assertIsNotNone(self.customer.churn_date)

    def test_condition_branches_to_yes_edge_when_true(self):
        nodes = [
            entry(),
            operator_node(
                "cond",
                "Condition",
                conditionAttribute="health_score",
                conditionOperator="less_than",
                conditionValue="5",
            ),
            action_node("yes_branch", "Churn Entity"),
            action_node("no_branch", "Create Task", taskTitle="Should not run"),
        ]
        edges = [
            edge("entry", "cond"),
            edge("cond", "yes_branch", label="Yes"),
            edge("cond", "no_branch", label="No"),
        ]
        run_scenario(self._scenario(nodes, edges), self.customer, ScenarioRun.TriggeredBy.MANUAL)

        self.customer.refresh_from_db()
        self.assertEqual(self.customer.lifecycle_stage, Customer.LifecycleStage.CHURN)
        self.assertFalse(Task.objects.filter(customer=self.customer).exists())

    def test_filter_stops_run_when_false(self):
        nodes = [
            entry(),
            operator_node(
                "filter",
                "Filter",
                conditionAttribute="health_score",
                conditionOperator="greater_than",
                conditionValue="9",
            ),
            action_node("after", "Churn Entity"),
        ]
        edges = [edge("entry", "filter"), edge("filter", "after")]
        run = run_scenario(
            self._scenario(nodes, edges), self.customer, ScenarioRun.TriggeredBy.MANUAL
        )

        self.customer.refresh_from_db()
        self.assertNotEqual(self.customer.lifecycle_stage, Customer.LifecycleStage.CHURN)
        self.assertEqual(run.log[-1]["detail"], "Filter condition false — run stopped here.")

    def test_mockup_only_action_is_skipped_not_failed(self):
        nodes = [entry(), action_node("n1", "Assign Playbook")]
        run = run_scenario(
            self._scenario(nodes, [edge("entry", "n1")]),
            self.customer,
            ScenarioRun.TriggeredBy.MANUAL,
        )

        self.assertEqual(run.status, ScenarioRun.Status.SUCCESS)
        self.assertEqual(run.log[0]["status"], "skipped")

    def test_one_failing_node_does_not_undo_earlier_side_effects(self):
        self.customer.email = ""
        self.customer.save()
        nodes = [
            entry(),
            action_node("n1", "Create Task", taskTitle="Already done"),
            action_node("n2", "Send Email", emailSubject="Hi", emailBody="Body"),
        ]
        edges = [edge("entry", "n1"), edge("n1", "n2")]
        run = run_scenario(
            self._scenario(nodes, edges), self.customer, ScenarioRun.TriggeredBy.MANUAL
        )

        self.assertEqual(run.status, ScenarioRun.Status.FAILED)
        self.assertTrue(Task.objects.filter(customer=self.customer, title="Already done").exists())
