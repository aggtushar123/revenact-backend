"""The facilitator: a session's decisions into the review queue."""

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.models import User
from services.copilot.models import (
    Conversation,
    CopilotSession,
    Message,
    SessionEvent,
    SessionInvite,
    SessionParticipant,
)
from services.customers.models import Customer, Task
from services.metrics import facilitator
from services.metrics.models import Proposal

from .test_proposals import _org

PATH = "services.metrics.facilitator.get_completion"


def _session(org, owner, teammate, customer):
    """A live session about `customer` where the owner opened, the teammate
    joined and took the action on."""
    conversation = Conversation.objects.create(
        organisation=org, user=owner, title="What do we do about Fine?"
    )
    session = CopilotSession.objects.create(
        conversation=conversation, customer=customer, status=CopilotSession.Status.LIVE
    )
    SessionParticipant.objects.create(session=session, user=owner)
    SessionInvite.objects.create(
        session=session,
        invited_user=teammate,
        invited_by=owner,
        status=SessionInvite.Status.ACCEPTED,
    )
    SessionParticipant.objects.create(session=session, user=teammate)
    Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content="Fine's champion just left. What should we do?",
    )
    Message.objects.create(
        conversation=conversation,
        role=Message.Role.ASSISTANT,
        content="Consider an exec sponsor call and a seat-usage review.",
    )
    reply = Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content="Agreed — I'll book the exec sponsor call this week.",
    )
    SessionEvent.objects.create(
        session=session, kind=SessionEvent.Kind.REDIRECTED, actor=teammate, message=reply
    )
    SessionEvent.objects.create(
        session=session,
        kind=SessionEvent.Kind.HANDED_OFF,
        actor=owner,
        payload={"to_user_id": teammate.id, "to_user_name": teammate.name, "note": "your account"},
    )
    return session


def _answer(customer_id):
    return f"""[
  {{"kind": "task", "title": "Book the exec sponsor call with Fine",
    "rationale": "Carl agreed to book it this week after the champion left.",
    "evidence": ["Carl: Agreed — I'll book the exec sponsor call this week."],
    "initiative_id": null,
    "action": {{"customer_id": {customer_id}, "title": "Exec sponsor call: Fine",
                "assignee": "Carl", "due_in_days": 7, "priority": "high"}}}}
]"""


class EvidenceTests(TestCase):
    def setUp(self):
        self.org, self.admin, self.carl, self.b, self.shaky = _org()
        self.fine = Customer.objects.get(organisation=self.org, name="Fine")
        self.session = _session(self.org, self.admin, self.carl, self.fine)

    def test_the_prompt_carries_the_transcript_with_authors_and_the_sessions_account(self):
        evidence = facilitator.build_evidence(self.session)
        prompt = facilitator.build_prompt(evidence)

        self.assertIn("Alice: Fine's champion just left.", prompt)
        self.assertIn("Copilot: Consider an exec sponsor call", prompt)
        self.assertIn("Carl: Agreed — I'll book the exec sponsor call", prompt)
        self.assertIn("Took part: Alice, Carl", prompt)
        self.assertIn('Alice handed off to Carl — "your account"', prompt)
        self.assertIn(f"About: Fine (account id {self.fine.id})", prompt)
        # Fine carries no downside, so the Ops agent never lists it — the
        # facilitator adds it so a task on it validates.
        self.assertIn(self.fine.id, {a["id"] for a in evidence["accounts"]})
        self.assertIn(f"- {self.fine.id}: Fine — owner Carl", prompt)

    def test_a_session_nobody_has_spoken_in_has_nothing_to_decide(self):
        Message.objects.filter(conversation=self.session.conversation).delete()

        with self.assertRaises(facilitator.NothingToDecideFrom):
            facilitator.build_evidence(self.session)


class CaptureTests(TestCase):
    def setUp(self):
        self.org, self.admin, self.carl, self.b, self.shaky = _org()
        self.fine = Customer.objects.get(organisation=self.org, name="Fine")
        self.session = _session(self.org, self.admin, self.carl, self.fine)

    def test_decisions_are_stored_as_proposals_tagged_with_the_session(self):
        with patch(PATH, return_value=_answer(self.fine.id)) as call:
            stored = facilitator.capture_decisions(self.session, requested_by=self.carl)

        self.assertEqual(call.call_args.kwargs["purpose"], "facilitator")
        self.assertEqual(len(stored), 1)
        proposal = stored[0]
        self.assertEqual(proposal.session, self.session)
        self.assertEqual(proposal.kind, "task")
        self.assertEqual(proposal.action["customer_id"], self.fine.id)
        self.assertEqual(proposal.action["assignee_name"], "Carl")
        self.assertEqual(proposal.generated_by, self.carl)
        self.assertEqual(proposal.status, Proposal.Status.PROPOSED)

    def test_a_session_where_nothing_was_decided_stores_nothing(self):
        with patch(PATH, return_value="[]"):
            self.assertEqual(facilitator.capture_decisions(self.session), [])


class DecisionsViewTests(APITestCase):
    def setUp(self):
        self.org, self.admin, self.carl, self.b, self.shaky = _org()
        self.fine = Customer.objects.get(organisation=self.org, name="Fine")
        self.session = _session(self.org, self.admin, self.carl, self.fine)
        self.stranger = User.objects.create_user(
            email="zed@acme.io", password="x", name="Zed", organisation=self.org
        )
        self.url = (
            f"/api/v1/copilot/conversations/{self.session.conversation_id}/session/decisions/"
        )

    def test_a_participant_captures_and_lists_the_sessions_decisions(self):
        self.client.force_authenticate(self.carl)
        with patch(PATH, return_value=_answer(self.fine.id)):
            response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        (row,) = response.data["proposals"]
        self.assertEqual(row["source"]["title"], "What do we do about Fine?")
        self.assertEqual(row["source"]["conversation_id"], self.session.conversation_id)

        listed = self.client.get(self.url)
        self.assertEqual([p["id"] for p in listed.data["proposals"]], [row["id"]])

    def test_someone_outside_the_session_cannot_see_or_ask(self):
        self.client.force_authenticate(self.stranger)
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_404_NOT_FOUND)
        with patch(PATH) as call:
            self.assertEqual(self.client.post(self.url).status_code, status.HTTP_404_NOT_FOUND)
        call.assert_not_called()

    def test_a_conversation_without_a_session_is_404(self):
        lone = Conversation.objects.create(organisation=self.org, user=self.admin, title="Solo")
        self.client.force_authenticate(self.admin)
        response = self.client.get(f"/api/v1/copilot/conversations/{lone.id}/session/decisions/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_a_silent_session_is_422_without_a_model_call(self):
        Message.objects.filter(conversation=self.session.conversation).delete()
        self.client.force_authenticate(self.admin)
        with patch(PATH) as call:
            response = self.client.post(self.url)
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        call.assert_not_called()

    def test_approving_in_the_review_queue_creates_the_task_and_keeps_the_source(self):
        self.client.force_authenticate(self.admin)
        with patch(PATH, return_value=_answer(self.fine.id)):
            (row,) = self.client.post(self.url).data["proposals"]

        response = self.client.post(f"/api/v1/metrics/proposals/{row['id']}/approve/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["proposal"]["source"]["session_id"], self.session.id)
        task = Task.objects.get(customer=self.fine)
        self.assertEqual(task.title, "Exec sponsor call: Fine")
        self.assertEqual(task.assignee_name, "Carl")
        self.assertEqual(task.due_date, timezone.localdate() + timedelta(days=7))
