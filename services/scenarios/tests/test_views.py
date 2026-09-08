"""Integration tier: through the real URLconf + real test DB."""

from django.core import mail
from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.customers.models import Customer
from services.scenarios.models import Scenario, ScenarioRun


def entry(event_trigger=None):
    return {
        "id": "entry",
        "type": "entry",
        "data": {"action": "Run Now", "label": "Start", "eventTrigger": event_trigger},
    }


def action_node(node_id, action, **data):
    return {"id": node_id, "type": "action", "data": {"action": action, "label": action, **data}}


class ScenarioListCreateTests(APITestCase):
    url = "/api/v1/scenarios/"

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.other_org = Organisation.objects.create(name="Other Inc")
        self.user = User.objects.create_user(
            email="alice@acme.io", password="supersecret1", name="Alice", organisation=self.org
        )

    def test_unauthenticated_cannot_list(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_list_only_returns_own_organisation(self):
        Scenario.objects.create(organisation=self.org, name="Mine")
        Scenario.objects.create(organisation=self.other_org, name="Not mine")
        self.client.force_authenticate(self.user)

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        names = [s["name"] for s in response.data]
        self.assertEqual(names, ["Mine"])

    def test_create_defaults_and_scopes_to_caller_organisation(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(self.url, {"name": "Onboarding Flow"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        scenario = Scenario.objects.get(pk=response.data["id"])
        self.assertEqual(scenario.organisation, self.org)
        self.assertEqual(scenario.apply_to, Scenario.ApplyTo.ORGANIZATIONS)
        self.assertFalse(scenario.is_active)


class ScenarioDetailTests(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.other_org = Organisation.objects.create(name="Other Inc")
        self.user = User.objects.create_user(
            email="alice@acme.io", password="supersecret1", name="Alice", organisation=self.org
        )
        self.scenario = Scenario.objects.create(organisation=self.org, name="Mine")
        self.foreign_scenario = Scenario.objects.create(
            organisation=self.other_org, name="Not mine"
        )

    def test_patch_updates_nodes_and_edges(self):
        self.client.force_authenticate(self.user)
        url = f"/api/v1/scenarios/{self.scenario.id}/"
        response = self.client.patch(
            url, {"nodes": [entry()], "edges": [], "is_active": True}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.nodes, [entry()])
        self.assertTrue(self.scenario.is_active)

    def test_404_for_scenario_outside_own_organisation(self):
        self.client.force_authenticate(self.user)
        url = f"/api/v1/scenarios/{self.foreign_scenario.id}/"
        self.assertEqual(self.client.get(url).status_code, status.HTTP_404_NOT_FOUND)

    def test_delete(self):
        self.client.force_authenticate(self.user)
        url = f"/api/v1/scenarios/{self.scenario.id}/"
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Scenario.objects.filter(pk=self.scenario.id).exists())


class ScenarioRunViewTests(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.user = User.objects.create_user(
            email="alice@acme.io", password="supersecret1", name="Alice", organisation=self.org
        )
        self.customer = Customer.objects.create(
            organisation=self.org, name="Globex", email="ops@globex.io"
        )
        self.scenario = Scenario.objects.create(
            organisation=self.org,
            nodes=[entry(), action_node("n1", "Send Email", emailSubject="Hi", emailBody="Body")],
            edges=[{"id": "e1", "source": "entry", "target": "n1"}],
        )

    def test_run_executes_and_returns_the_log(self):
        self.client.force_authenticate(self.user)
        url = f"/api/v1/scenarios/{self.scenario.id}/run/"
        response = self.client.post(url, {"customer_id": self.customer.id}, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], "success")
        self.assertEqual(response.data["customer"]["id"], self.customer.id)
        self.assertEqual(len(mail.outbox), 1)

    def test_run_rejects_non_organizations_scenario(self):
        self.scenario.apply_to = Scenario.ApplyTo.ACCOUNTS
        self.scenario.save()
        self.client.force_authenticate(self.user)
        url = f"/api/v1/scenarios/{self.scenario.id}/run/"
        response = self.client.post(url, {"customer_id": self.customer.id}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_run_history_lists_newest_first(self):
        self.client.force_authenticate(self.user)
        run_url = f"/api/v1/scenarios/{self.scenario.id}/run/"
        self.client.post(run_url, {"customer_id": self.customer.id}, format="json")
        self.client.post(run_url, {"customer_id": self.customer.id}, format="json")

        response = self.client.get(f"/api/v1/scenarios/{self.scenario.id}/runs/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)
        self.assertGreaterEqual(response.data[0]["id"], response.data[1]["id"])


class OnEventSignalTests(APITestCase):
    """The one On Event trigger that's real — see signals.py's own
    docstring. `transaction.on_commit` callbacks don't fire inside
    Django's default `TestCase` (it wraps each test in a transaction
    that's rolled back, never committed) — APITestCase shares that
    behavior, so these use `captureOnCommitCallbacks` to run them
    immediately, same as Django's own docs recommend for testing
    `on_commit` code."""

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")

    def _on_event_scenario(self, is_active):
        return Scenario.objects.create(
            organisation=self.org,
            is_active=is_active,
            nodes=[
                entry(event_trigger="new_entity"),
                action_node("n1", "Create Task", taskTitle="Welcome"),
            ],
            edges=[{"id": "e1", "source": "entry", "target": "n1"}],
        )

    def test_active_scenario_runs_on_customer_creation(self):
        self._on_event_scenario(is_active=True)
        with self.captureOnCommitCallbacks(execute=True):
            customer = Customer.objects.create(organisation=self.org, name="Globex")

        self.assertEqual(ScenarioRun.objects.filter(customer=customer).count(), 1)
        run = ScenarioRun.objects.get(customer=customer)
        self.assertEqual(run.triggered_by, ScenarioRun.TriggeredBy.EVENT)

    def test_inactive_scenario_does_not_run(self):
        self._on_event_scenario(is_active=False)
        with self.captureOnCommitCallbacks(execute=True):
            customer = Customer.objects.create(organisation=self.org, name="Globex")

        self.assertEqual(ScenarioRun.objects.filter(customer=customer).count(), 0)

    def test_does_not_run_for_a_different_organisation(self):
        other_org = Organisation.objects.create(name="Other Inc")
        self._on_event_scenario(is_active=True)
        with self.captureOnCommitCallbacks(execute=True):
            customer = Customer.objects.create(organisation=other_org, name="Initech")

        self.assertEqual(ScenarioRun.objects.filter(customer=customer).count(), 0)


class ScenarioOwnershipScopingTests(APITestCase):
    """A scenario is a tenant-wide automation and stays visible to
    everyone. What's scoped is which customers you can point it at, and
    whose runs you can read back — see services/customers/scoping.py."""

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
        self.admin = User.objects.create_user(
            email="alice@acme.io",
            password="supersecret1",
            name="Alice",
            organisation=self.org,
            role=User.Role.ADMIN,
        )
        self.theirs = Customer.objects.create(
            organisation=self.org,
            name="Theirs",
            email="ops@theirs.example",
            owner=self.other,
            lifecycle_stage="live",
        )
        self.scenario = Scenario.objects.create(
            organisation=self.org,
            nodes=[entry(), action_node("n1", "Send Email", emailSubject="Hi", emailBody="Body")],
            edges=[{"id": "e1", "source": "entry", "target": "n1"}],
        )
        self.run_url = f"/api/v1/scenarios/{self.scenario.id}/run/"

    def test_cannot_run_against_a_customer_you_cannot_see(self):
        """The write leak this closes: the engine sets lifecycle_stage
        and churn_date and sends real mail, so an ungated target let any
        member act on an account they couldn't open."""
        self.client.force_authenticate(self.csm)

        response = self.client.post(self.run_url, {"customer_id": self.theirs.id}, format="json")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(ScenarioRun.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_can_run_against_your_own_customer(self):
        mine = Customer.objects.create(
            organisation=self.org, name="Mine", email="ops@mine.example", owner=self.csm
        )
        self.client.force_authenticate(self.csm)

        response = self.client.post(self.run_url, {"customer_id": mine.id}, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_a_capability_holder_can_run_against_anyone(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(self.run_url, {"customer_id": self.theirs.id}, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_run_history_hides_other_peoples_customers(self):
        """Each row names its customer and the log quotes contact
        addresses ("Emailed x@y: ..."), so the history leaks even when
        the scenario itself is legitimately shared."""
        self.client.force_authenticate(self.admin)
        self.client.post(self.run_url, {"customer_id": self.theirs.id}, format="json")

        self.client.force_authenticate(self.csm)
        response = self.client.get(f"/api/v1/scenarios/{self.scenario.id}/runs/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])

    def test_run_history_still_shows_your_own(self):
        mine = Customer.objects.create(
            organisation=self.org, name="Mine", email="ops@mine.example", owner=self.csm
        )
        self.client.force_authenticate(self.csm)
        self.client.post(self.run_url, {"customer_id": mine.id}, format="json")

        response = self.client.get(f"/api/v1/scenarios/{self.scenario.id}/runs/")

        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["customer"]["name"], "Mine")

    def test_a_run_whose_customer_was_deleted_stays_visible(self):
        """`ScenarioRun.customer` is SET_NULL. An orphaned run names
        nobody, so it has nothing to hide and shouldn't silently vanish
        from the history."""
        mine = Customer.objects.create(
            organisation=self.org, name="Mine", email="ops@mine.example", owner=self.csm
        )
        self.client.force_authenticate(self.csm)
        self.client.post(self.run_url, {"customer_id": mine.id}, format="json")
        mine.delete()

        response = self.client.get(f"/api/v1/scenarios/{self.scenario.id}/runs/")

        self.assertEqual(len(response.data), 1)
        self.assertIsNone(response.data[0]["customer"])

    def test_the_scenario_itself_stays_visible_to_everyone(self):
        """Deliberate: a Scenario has no owner — it's a tenant-wide
        automation, not a per-account record."""
        self.client.force_authenticate(self.csm)

        response = self.client.get("/api/v1/scenarios/")

        self.assertEqual(len(response.data), 1)
