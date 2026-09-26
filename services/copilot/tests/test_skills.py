"""The skills catalogue: every purpose described, figures beside it."""

from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.copilot import skills, usage
from services.copilot.models import Conversation, Message, ModelCall
from services.metrics.models import Brief, Proposal


def _org():
    org = Organisation.objects.create(name="Acme Inc", currency="USD")
    admin = User.objects.create_user(
        email="alice@acme.io", password="x", name="Alice", organisation=org, role=User.Role.ADMIN
    )
    csm = User.objects.create_user(
        email="carl@acme.io", password="x", name="Carl", organisation=org, role=User.Role.CSM
    )
    return org, admin, csm


class CatalogueTests(TestCase):
    def setUp(self):
        self.org, self.admin, self.csm = _org()

    def test_every_purpose_a_call_can_run_under_is_described(self):
        self.assertEqual(set(skills.BY_PURPOSE), set(usage.PURPOSES))
        for skill in skills.SKILLS:
            self.assertTrue(skill.reads and skill.may and skill.never, skill.purpose)

    def test_usage_last_run_and_produced_sit_beside_each_skill(self):
        conversation = Conversation.objects.create(organisation=self.org, user=self.admin)
        Message.objects.create(conversation=conversation, role="user", content="hi")
        Message.objects.create(conversation=conversation, role="assistant", content="hello")
        Brief.objects.create(
            organisation=self.org,
            as_of=timezone.localdate(),
            headline="h",
            body="b",
            evidence={},
            generated_at=timezone.now(),
        )
        Proposal.objects.create(
            organisation=self.org,
            batch="b",
            kind="task",
            title="t",
            rationale="r",
            action={},
            status=Proposal.Status.APPROVED,
        )
        ModelCall.objects.create(
            organisation=self.org,
            user=self.admin,
            purpose="brief",
            input_tokens=1000,
            output_tokens=200,
            outcome=ModelCall.Outcome.OK,
        )
        other = Organisation.objects.create(name="Globex", currency="USD")
        Brief.objects.create(
            organisation=other,
            as_of=timezone.localdate(),
            headline="h",
            body="b",
            evidence={},
            generated_at=timezone.now(),
        )

        by = {s["purpose"]: s for s in skills.catalogue(self.org)["skills"]}

        self.assertEqual(by["copilot"]["produced"], {"label": "replies", "count": 1})
        self.assertEqual(by["brief"]["produced"]["count"], 1)
        self.assertEqual(by["brief"]["usage"]["spent"], 1200)
        self.assertEqual(by["brief"]["last_run"]["user"], "Alice")
        self.assertEqual(by["brief"]["last_run"]["outcome"], "ok")
        self.assertEqual(
            by["proposals"]["produced"], {"label": "proposals", "count": 1, "approved": 1}
        )
        self.assertEqual(by["facilitator"]["produced"]["count"], 0)
        self.assertIsNone(by["classification"]["produced"])
        self.assertIsNone(by["classification"]["last_run"])

    def test_the_dashboard_has_its_own_purpose_and_skill(self):
        self.assertEqual(usage.PURPOSES["dashboard"], "Ask Revenact on the Dashboard")
        skill = skills.BY_PURPOSE["dashboard"]
        self.assertEqual(skill.surface, "/dashboard")
        self.assertIn("See accounts outside the asker's filtered book", skill.never)


class ViewTests(APITestCase):
    def test_organizations_has_its_own_purpose_and_skill(self):
        self.assertEqual(usage.PURPOSES["organizations"], "Ask Revenact on Organizations")
        skill = skills.BY_PURPOSE["organizations"]
        self.assertEqual(skill.surface, "/organizations")
        self.assertIn("See accounts outside the asker's filtered book", skill.never)

    def test_every_ask_surface_is_metered_under_a_described_purpose(self):
        from services.copilot.ask import SURFACES

        self.assertEqual(
            {surface.purpose for surface in SURFACES.values()}, {"dashboard", "organizations"}
        )
        for surface in SURFACES.values():
            self.assertIn(surface.purpose, skills.BY_PURPOSE)

    def setUp(self):
        self.org, self.admin, self.csm = _org()

    def test_gated_like_usage(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/v1/copilot/skills/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [s["purpose"] for s in response.data["skills"]], [s.purpose for s in skills.SKILLS]
        )
        self.client.force_authenticate(self.csm)
        self.assertEqual(
            self.client.get("/api/v1/copilot/skills/").status_code, status.HTTP_403_FORBIDDEN
        )
