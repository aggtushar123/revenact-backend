"""The knowledge graph: real relations, real figures, gated."""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from services.customers.models import Task
from services.metrics import graph
from services.metrics.models import Initiative, Proposal

from .test_proposals import _org


class GraphTests(TestCase):
    def setUp(self):
        self.org, self.admin, self.carl, self.b, self.shaky = _org()
        self.initiative = Initiative.objects.create(
            organisation=self.org,
            title="Halve the ARR at risk on Product B",
            metric="at_risk_arr",
            dimension="product",
            member=str(self.b.id),
            member_label="Product B",
            target_value=10000,
            target_by=timezone.localdate() + timedelta(days=60),
            baseline_as_of=timezone.localdate(),
            owner=self.carl,
        )
        self.proposal = Proposal.objects.create(
            organisation=self.org,
            batch="b",
            kind="task",
            title="Save play on Pizza Hut",
            rationale="r",
            action={"customer_id": self.shaky.id, "title": "Save play"},
            initiative=self.initiative,
        )
        Task.objects.create(
            customer=self.shaky,
            title="Call them",
            assignee_name="Carl",
            due_date=timezone.localdate(),
            priority="high",
        )

    def test_nodes_carry_the_dashboards_figures_and_edges_only_real_relations(self):
        payload = graph.build_graph(self.org)
        by = {n["id"]: n for n in payload["nodes"]}
        edges = {(e["from"], e["to"], e["kind"]) for e in payload["edges"]}

        shaky = by[f"customer:{self.shaky.id}"]
        self.assertEqual(shaky["arr"], 100000.0)
        self.assertGreater(shaky["downside"], 0)
        self.assertEqual(shaky["open_tasks"], 1)
        self.assertEqual(shaky["health_category"], "poor")

        product = by[f"product:{self.b.id}"]
        self.assertEqual(product["customers"], 2)
        self.assertEqual(product["arr"], 300000.0)
        self.assertEqual(product["downside"], shaky["downside"])
        owner = by[f"owner:{self.carl.id}"]
        self.assertEqual(owner["label"], "Carl")
        self.assertEqual(owner["customers"], 2)

        initiative = by[f"initiative:{self.initiative.id}"]
        self.assertEqual(initiative["metric_label"], "ARR at risk")
        self.assertEqual(initiative["owner"], "Carl")
        proposal = by[f"proposal:{self.proposal.id}"]
        self.assertEqual(proposal["proposal_kind"], "task")

        self.assertIn((f"customer:{self.shaky.id}", f"product:{self.b.id}", "runs_on"), edges)
        self.assertIn((f"owner:{self.carl.id}", f"customer:{self.shaky.id}", "owns"), edges)
        self.assertIn(
            (f"initiative:{self.initiative.id}", f"product:{self.b.id}", "targets"), edges
        )
        self.assertIn(
            (f"proposal:{self.proposal.id}", f"customer:{self.shaky.id}", "acts_on"), edges
        )
        self.assertIn(
            (f"proposal:{self.proposal.id}", f"initiative:{self.initiative.id}", "serves"), edges
        )
        # Every edge points at a node that exists.
        for source, target, _ in edges:
            self.assertIn(source, by)
            self.assertIn(target, by)

    def test_closed_decisions_and_decided_proposals_are_not_drawn(self):
        self.initiative.status = Initiative.Status.DONE
        self.initiative.save()
        self.proposal.status = Proposal.Status.REJECTED
        self.proposal.save()

        kinds = {n["kind"] for n in graph.build_graph(self.org)["nodes"]}
        self.assertEqual(kinds, {"customer", "product", "owner"})


class ViewTests(APITestCase):
    def setUp(self):
        self.org, self.admin, self.carl, self.b, self.shaky = _org()

    def test_gated_on_view_all_accounts(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/v1/metrics/graph/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["currency"], "USD")
        self.assertTrue(any(n["kind"] == "customer" for n in response.data["nodes"]))
        self.client.force_authenticate(self.carl)
        self.assertEqual(
            self.client.get("/api/v1/metrics/graph/").status_code, status.HTTP_403_FORBIDDEN
        )
