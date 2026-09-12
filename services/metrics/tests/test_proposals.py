"""The Ops agent and the review queue."""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.copilot.anthropic_client import CopilotNotConfigured
from services.customers.models import Customer, Product, Task
from services.metrics import proposals
from services.metrics.models import Initiative, Proposal

PATH = "services.metrics.proposals.get_completion"


def _org():
    org = Organisation.objects.create(name="Acme Inc", currency="USD")
    admin = User.objects.create_user(
        email="alice@acme.io", password="x", name="Alice", organisation=org, role=User.Role.ADMIN
    )
    carl = User.objects.create_user(
        email="carl@acme.io", password="x", name="Carl", organisation=org, role=User.Role.CSM
    )
    b = Product.objects.create(organisation=org, name="Product B")
    shaky = Customer.objects.create(
        organisation=org,
        name="Pizza Hut",
        owner=carl,
        primary_product=b,
        arr_billed_at_account=Decimal(100_000),
        health_score=Decimal("2.0"),
        renewal_date=timezone.localdate() + timedelta(days=30),
    )
    Customer.objects.create(
        organisation=org,
        name="Fine",
        owner=carl,
        primary_product=b,
        arr_billed_at_account=Decimal(200_000),
        health_score=Decimal("8.5"),
    )
    return org, admin, carl, b, shaky


def _answer(customer_id, product_id, initiative_id="null"):
    return f"""[
  {{"kind": "task", "title": "Run a save play on Pizza Hut before renewal",
    "rationale": "Pizza Hut carries the downside and renews in 30 days.",
    "evidence": ["Pizza Hut: renews in 30 days, health poor"],
    "initiative_id": {initiative_id},
    "action": {{"customer_id": {customer_id}, "title": "Save play: Pizza Hut", "assignee": "Carl",
                "due_in_days": 7, "priority": "high"}}}},
  {{"kind": "initiative", "title": "Halve ARR at risk on Product B",
    "rationale": "Product B carries all of the risk.", "evidence": ["at_risk_arr by product"],
    "initiative_id": null,
    "action": {{"metric": "at_risk_arr", "dimension": "product", "member": "{product_id}",
                "target_value": 20000, "target_in_days": 60}}}},
  {{"kind": "task", "title": "Email everyone", "rationale": "x", "evidence": [],
    "initiative_id": null,
    "action": {{"customer_id": 999999, "title": "Nope", "assignee": "Nobody", "due_in_days": 3,
                "priority": "high"}}}}
]"""


class EvidenceTests(TestCase):
    def setUp(self):
        self.org, self.admin, self.carl, self.b, self.shaky = _org()

    def test_the_agent_sees_the_accounts_carrying_the_downside_and_who_owns_them(self):
        evidence = proposals.build_evidence(self.org)
        prompt = proposals.build_prompt(evidence)

        self.assertEqual([a["name"] for a in evidence["accounts"]], ["Pizza Hut"])
        self.assertEqual(evidence["accounts"][0]["owner"], "Carl")
        self.assertIn(f"- {self.shaky.id}: Pizza Hut — owner Carl, product Product B", prompt)
        self.assertIn("renews in 30 days", prompt)
        self.assertIn("- Carl", prompt)
        # The healthy account carries no downside and is not offered as a target.
        self.assertNotIn("Fine", prompt)

    def test_an_empty_book_has_nothing_to_propose_from(self):
        empty = Organisation.objects.create(name="New Inc", currency="USD")

        with self.assertRaises(proposals.NothingToProposeFrom):
            proposals.build_evidence(empty)


class GenerationTests(TestCase):
    def setUp(self):
        self.org, self.admin, self.carl, self.b, self.shaky = _org()

    def test_valid_proposals_are_stored_and_invalid_ones_dropped(self):
        with patch(PATH, return_value=_answer(self.shaky.id, self.b.id)):
            with self.assertLogs("services.metrics.proposals", "WARNING") as logs:
                stored = proposals.generate_proposals(self.org, generated_by=self.admin)

        self.assertEqual([p.kind for p in stored], ["task", "initiative"])
        self.assertIn("999999", logs.output[0])
        task = stored[0]
        self.assertEqual(task.action["customer_id"], self.shaky.id)
        self.assertEqual(task.action["assignee_name"], "Carl")
        self.assertEqual(task.action["priority"], "high")
        self.assertEqual(
            task.action["due_date"], (timezone.localdate() + timedelta(days=7)).isoformat()
        )
        initiative = stored[1]
        self.assertEqual(initiative.action["member_label"], "Product B")
        self.assertEqual(initiative.action["target_value"], 20000.0)
        self.assertTrue(all(p.batch == stored[0].batch for p in stored))
        self.assertTrue(all(p.status == "proposed" for p in stored))

    def test_an_unknown_assignee_falls_back_to_the_accounts_owner(self):
        answer = _answer(self.shaky.id, self.b.id).replace(
            '"assignee": "Carl"', '"assignee": "Zed"'
        )
        with (
            patch(PATH, return_value=answer),
            self.assertLogs("services.metrics.proposals", "WARNING"),
        ):
            stored = proposals.generate_proposals(self.org)

        self.assertEqual(stored[0].action["assignee_name"], "Carl")

    def test_approving_a_task_that_serves_a_decision_links_the_work_to_it(self):
        open_one = Initiative.objects.create(
            organisation=self.org,
            title="Open",
            metric="at_risk_arr",
            target_value=1,
            target_by=timezone.localdate() + timedelta(days=30),
            baseline_as_of=timezone.localdate(),
        )
        with patch(PATH, return_value=_answer(self.shaky.id, self.b.id, initiative_id=open_one.id)):
            with self.assertLogs("services.metrics.proposals", "WARNING"):
                stored = proposals.generate_proposals(self.org)

        proposals.approve(stored[0], self.admin)

        task = Task.objects.get(customer=self.shaky)
        self.assertEqual(task.initiative, open_one)

    def test_a_link_to_an_open_decision_is_kept_and_a_bogus_one_dropped(self):
        open_one = Initiative.objects.create(
            organisation=self.org,
            title="Open",
            metric="at_risk_arr",
            target_value=1,
            target_by=timezone.localdate() + timedelta(days=30),
            baseline_as_of=timezone.localdate(),
        )
        with patch(PATH, return_value=_answer(self.shaky.id, self.b.id, initiative_id=open_one.id)):
            with self.assertLogs("services.metrics.proposals", "WARNING"):
                stored = proposals.generate_proposals(self.org)

        self.assertEqual(stored[0].initiative, open_one)
        with patch(PATH, return_value=_answer(self.shaky.id, self.b.id, initiative_id=999)):
            with self.assertLogs("services.metrics.proposals", "WARNING"):
                stored = proposals.generate_proposals(self.org)
        self.assertIsNone(stored[0].initiative)


class DecisionTests(TestCase):
    def setUp(self):
        self.org, self.admin, self.carl, self.b, self.shaky = _org()
        with patch(PATH, return_value=_answer(self.shaky.id, self.b.id)):
            with self.assertLogs("services.metrics.proposals", "WARNING"):
                self.task_proposal, self.initiative_proposal = proposals.generate_proposals(
                    self.org
                )

    def test_approving_a_task_creates_it_on_the_account_and_records_the_result(self):
        proposals.approve(self.task_proposal, self.admin, "Go")

        task = Task.objects.get()
        self.assertEqual(task.customer, self.shaky)
        self.assertEqual(task.title, "Save play: Pizza Hut")
        self.assertEqual(task.assignee_name, "Carl")
        self.assertEqual(task.priority, "high")
        self.task_proposal.refresh_from_db()
        self.assertEqual(self.task_proposal.status, "approved")
        self.assertEqual(
            self.task_proposal.result, {"task_id": task.id, "customer_id": self.shaky.id}
        )
        self.assertEqual(self.task_proposal.decided_by, self.admin)

    def test_approving_an_initiative_writes_it_with_a_starting_line(self):
        proposals.approve(self.initiative_proposal, self.admin)

        initiative = Initiative.objects.get()
        self.assertEqual(initiative.member_label, "Product B")
        self.assertEqual(initiative.target_value, Decimal("20000"))
        self.assertGreater(initiative.baseline_value, 0)
        self.assertEqual(initiative.created_by, self.admin)
        self.assertEqual(initiative.hypothesis, "Product B carries all of the risk.")

    def test_a_proposal_is_decided_once(self):
        proposals.reject(self.task_proposal, self.admin, "Not now")

        with self.assertRaises(proposals.AlreadyDecided):
            proposals.approve(self.task_proposal, self.admin)
        self.assertFalse(Task.objects.exists())


class ProposalAPITests(APITestCase):
    def setUp(self):
        self.org, self.admin, self.carl, self.b, self.shaky = _org()
        self.client.force_authenticate(self.admin)

    def test_a_csm_is_refused(self):
        self.client.force_authenticate(self.carl)

        self.assertEqual(
            self.client.get("/api/v1/metrics/proposals/").status_code, status.HTTP_403_FORBIDDEN
        )

    def test_generate_then_review(self):
        with patch(PATH, return_value=_answer(self.shaky.id, self.b.id)):
            with self.assertLogs("services.metrics.proposals", "WARNING"):
                response = self.client.post("/api/v1/metrics/proposals/generate/")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        queue = self.client.get("/api/v1/metrics/proposals/").data
        self.assertEqual(queue["pending"], 2)
        self.assertEqual(queue["proposals"][0]["kind"], "task")
        self.assertEqual(queue["proposals"][0]["action"]["customer_name"], "Pizza Hut")

        task_id = queue["proposals"][0]["id"]
        approved = self.client.post(
            f"/api/v1/metrics/proposals/{task_id}/approve/", {"note": "Do it"}, format="json"
        ).data["proposal"]
        self.assertEqual(approved["status"], "approved")
        self.assertIn("task_id", approved["result"])
        self.assertEqual(approved["decided_by"], "Alice")

        again = self.client.post(f"/api/v1/metrics/proposals/{task_id}/reject/", {}, format="json")
        self.assertEqual(again.status_code, status.HTTP_409_CONFLICT)

        queue = self.client.get("/api/v1/metrics/proposals/").data
        self.assertEqual(queue["pending"], 1)
        # Proposed first, decided after.
        self.assertEqual([p["status"] for p in queue["proposals"]], ["proposed", "approved"])

    def test_a_missing_provider_is_a_503(self):
        with patch(PATH, side_effect=CopilotNotConfigured("No provider configured.")):
            response = self.client.post("/api/v1/metrics/proposals/generate/")

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)

    def test_another_organisations_proposal_is_a_404(self):
        other = Organisation.objects.create(name="Other Inc", currency="USD")
        theirs = Proposal.objects.create(
            organisation=other, batch="x", kind="task", title="t", rationale="r", action={}
        )

        self.assertEqual(
            self.client.post(f"/api/v1/metrics/proposals/{theirs.id}/approve/").status_code,
            status.HTTP_404_NOT_FOUND,
        )
