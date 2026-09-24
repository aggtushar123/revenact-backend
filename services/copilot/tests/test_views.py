"""Integration tier: through the real URLconf + real test DB. The
Anthropic API call itself is always mocked — never a real, paid, live
call during the test suite (see anthropic_client's own docstring)."""

from unittest.mock import patch

from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.copilot.anthropic_client import CopilotRequestFailed
from services.copilot.models import Conversation, Message
from services.customers.models import Customer, Note


class ConversationListViewTests(APITestCase):
    url = "/api/v1/copilot/conversations/"

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.user = User.objects.create_user(
            email="alice@acme.io", password="supersecret1", name="Alice", organisation=self.org
        )
        self.other_user = User.objects.create_user(
            email="bob@acme.io", password="supersecret1", name="Bob", organisation=self.org
        )

    def test_unauthenticated_cannot_list(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_list_only_returns_the_callers_own_conversations(self):
        Conversation.objects.create(organisation=self.org, user=self.user, title="Mine")
        Conversation.objects.create(organisation=self.org, user=self.other_user, title="Not mine")
        self.client.force_authenticate(self.user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([c["title"] for c in response.data], ["Mine"])


class ConversationDetailViewTests(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.user = User.objects.create_user(
            email="alice@acme.io", password="supersecret1", name="Alice", organisation=self.org
        )
        self.other_user = User.objects.create_user(
            email="bob@acme.io", password="supersecret1", name="Bob", organisation=self.org
        )
        self.conversation = Conversation.objects.create(
            organisation=self.org, user=self.user, title="Mine"
        )
        Message.objects.create(conversation=self.conversation, role=Message.Role.USER, content="Hi")

    def _url(self, conversation):
        return f"/api/v1/copilot/conversations/{conversation.id}/"

    def test_retrieve_includes_nested_messages(self):
        self.client.force_authenticate(self.user)
        response = self.client.get(self._url(self.conversation))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["messages"]), 1)
        self.assertEqual(response.data["messages"][0]["content"], "Hi")

    def test_404_for_another_users_conversation(self):
        foreign = Conversation.objects.create(
            organisation=self.org, user=self.other_user, title="Not mine"
        )
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.get(self._url(foreign)).status_code, status.HTTP_404_NOT_FOUND)

    def test_delete(self):
        self.client.force_authenticate(self.user)
        response = self.client.delete(self._url(self.conversation))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Conversation.objects.filter(pk=self.conversation.id).exists())


class SendMessageViewTests(APITestCase):
    url = "/api/v1/copilot/messages/"

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.user = User.objects.create_user(
            email="alice@acme.io", password="supersecret1", name="Alice", organisation=self.org
        )
        self.other_user = User.objects.create_user(
            email="bob@acme.io", password="supersecret1", name="Bob", organisation=self.org
        )
        self.client.force_authenticate(self.user)

    def test_blank_content_is_rejected(self):
        response = self.client.post(self.url, {"content": "   "}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_oversized_content_is_rejected(self):
        response = self.client.post(self.url, {"content": "x" * 8001}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_disabled_ai_agent_is_refused(self):
        self.org.ai_agent_enabled = False
        self.org.save()

        response = self.client.post(self.url, {"content": "Hello"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # Pins the provider explicitly, not just clearing ANTHROPIC_API_KEY —
    # since Copilot: support AWS Bedrock (see anthropic_client.py), a
    # real ambient .env with COPILOT_LLM_PROVIDER=bedrock (and real AWS
    # credentials) would otherwise make this test silently pass through
    # to a real, live Bedrock call instead of hitting the "not
    # configured" short-circuit it's actually testing — caught live when
    # this exact thing happened after Bedrock was configured for real.
    @override_settings(COPILOT_LLM_PROVIDER="anthropic", ANTHROPIC_API_KEY="")
    def test_not_configured_returns_503_and_leaves_no_trace(self):
        response = self.client.post(self.url, {"content": "Hello"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        # A failed send creates nothing — no orphaned Conversation the
        # sidebar could never learn the id of (see SendMessageView's
        # own docstring on why this was worth being deliberate about).
        self.assertEqual(Conversation.objects.count(), 0)

    @patch("services.copilot.views.get_completion")
    def test_a_failed_request_returns_502_and_leaves_no_trace(self, mock_get_completion):
        mock_get_completion.side_effect = CopilotRequestFailed("rate limited")

        response = self.client.post(self.url, {"content": "Hello"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(response.data["detail"], "rate limited")
        self.assertEqual(Conversation.objects.count(), 0)
        self.assertEqual(Message.objects.count(), 0)

    @patch("services.copilot.views.get_completion")
    def test_a_failed_request_on_an_existing_conversation_adds_no_messages(
        self, mock_get_completion
    ):
        mock_get_completion.side_effect = CopilotRequestFailed("rate limited")
        conversation = Conversation.objects.create(
            organisation=self.org, user=self.user, title="Existing"
        )
        Message.objects.create(conversation=conversation, role=Message.Role.USER, content="First")

        response = self.client.post(
            self.url, {"conversation_id": conversation.id, "content": "Second"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(conversation.messages.count(), 1)

    @patch("services.copilot.views.get_completion")
    def test_first_message_creates_a_conversation_titled_from_it(self, mock_get_completion):
        mock_get_completion.return_value = "Hi there!"

        response = self.client.post(self.url, {"content": "What's my churn risk?"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        conversation = Conversation.objects.get(pk=response.data["id"])
        self.assertEqual(conversation.title, "What's my churn risk?")
        self.assertEqual(conversation.user, self.user)
        self.assertEqual(conversation.organisation, self.org)
        roles = [m["role"] for m in response.data["messages"]]
        self.assertEqual(roles, ["user", "assistant"])
        self.assertEqual(response.data["messages"][1]["content"], "Hi there!")

    @patch("services.copilot.views.get_completion")
    def test_existing_conversation_id_appends_instead_of_creating_a_new_one(
        self, mock_get_completion
    ):
        mock_get_completion.return_value = "Second reply"
        conversation = Conversation.objects.create(
            organisation=self.org, user=self.user, title="Existing"
        )
        Message.objects.create(conversation=conversation, role=Message.Role.USER, content="First")
        Message.objects.create(
            conversation=conversation, role=Message.Role.ASSISTANT, content="First reply"
        )

        response = self.client.post(
            self.url, {"conversation_id": conversation.id, "content": "Second"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Conversation.objects.count(), 1)
        self.assertEqual(len(response.data["messages"]), 4)

        # Prior history was replayed to the model, not just the new turn.
        sent_messages = mock_get_completion.call_args.kwargs["messages"]
        self.assertEqual([m["content"] for m in sent_messages], ["First", "First reply", "Second"])

    def test_404_for_another_users_conversation_id(self):
        foreign = Conversation.objects.create(
            organisation=self.org, user=self.other_user, title="Not mine"
        )
        response = self.client.post(
            self.url, {"conversation_id": foreign.id, "content": "Hi"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @patch("services.copilot.views.get_completion")
    def test_tone_and_org_data_are_included_in_the_system_prompt(self, mock_get_completion):
        mock_get_completion.return_value = "Reply"
        self.org.ai_agent_tone = Organisation.AgentTone.FRIENDLY
        self.org.save()

        self.client.post(self.url, {"content": "Hello"}, format="json")

        system_prompt = mock_get_completion.call_args.kwargs["system"]
        self.assertIn("warm, friendly", system_prompt)
        self.assertIn("Real-data summary", system_prompt)


class MessageSourcesTests(APITestCase):
    """An answer carries the records it was built from, so the chat can
    show where it came from — the difference between "trust me" and
    "here's the note that says so"."""

    url = "/api/v1/copilot/messages/"

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.user = User.objects.create_user(
            email="alice@acme.io", password="supersecret1", name="Alice", organisation=self.org
        )
        self.customer = Customer.objects.create(
            organisation=self.org, name="Globex", owner=self.user, health_score="3.0"
        )
        self.note = Note.objects.create(
            customer=self.customer,
            title="Commercial Negotiation Summary",
            author_name="Edgar Holmes",
            body="Customer requested 15% discount for a 3-year commitment.",
            logged_at="2026-03-15",
        )
        self.client.force_authenticate(self.user)

    @patch("services.copilot.views.get_completion")
    def test_the_answer_cites_the_records_it_was_built_from(self, mock_get_completion):
        mock_get_completion.return_value = "They asked for a 15% discount."

        response = self.client.post(self.url, {"content": "What's happening?"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        assistant = Message.objects.get(role=Message.Role.ASSISTANT)
        self.assertEqual(len(assistant.sources), 1)
        self.assertEqual(assistant.sources[0]["type"], "note")
        self.assertEqual(assistant.sources[0]["id"], self.note.id)

    @patch("services.copilot.views.get_completion")
    def test_the_user_turn_carries_no_sources(self, mock_get_completion):
        mock_get_completion.return_value = "Sure."

        self.client.post(self.url, {"content": "What's happening?"}, format="json")

        self.assertEqual(Message.objects.get(role=Message.Role.USER).sources, [])

    @patch("services.copilot.views.get_completion")
    def test_sources_come_back_on_the_response(self, mock_get_completion):
        mock_get_completion.return_value = "They asked for a 15% discount."

        response = self.client.post(self.url, {"content": "What's happening?"}, format="json")

        cited = response.data["messages"][-1]["sources"]
        self.assertEqual(cited[0]["label"], "Commercial Negotiation Summary")
        self.assertEqual(cited[0]["company"], "Globex")

    @patch("services.copilot.views.get_completion")
    def test_an_answer_with_nothing_to_quote_cites_nothing(self, mock_get_completion):
        """Better an empty citation list than an invented one — a
        question about the book as a whole isn't grounded in any
        particular record."""
        self.note.delete()
        mock_get_completion.return_value = "Your book looks healthy."

        self.client.post(self.url, {"content": "How am I doing?"}, format="json")

        self.assertEqual(Message.objects.get(role=Message.Role.ASSISTANT).sources, [])

    @patch("services.copilot.views.get_completion")
    def test_a_citation_survives_its_record_being_deleted(self, mock_get_completion):
        """Snapshots, not foreign keys — an answer given in March
        shouldn't lose its citation because someone tidied up in April.
        The link simply stops resolving."""
        mock_get_completion.return_value = "They asked for a 15% discount."
        self.client.post(self.url, {"content": "What's happening?"}, format="json")

        self.note.delete()

        assistant = Message.objects.get(role=Message.Role.ASSISTANT)
        self.assertEqual(assistant.sources[0]["label"], "Commercial Negotiation Summary")


class DashboardFieldsSerializationTests(APITestCase):
    origin = {
        "surface": "dashboard",
        "area": "revenue",
        "view": "forecast",
        "filters": {"owner": "", "lifecycle": "", "customer": ""},
    }

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.user = User.objects.create_user(
            email="alice@acme.io", password="supersecret1", name="Alice", organisation=self.org
        )
        self.conversation = Conversation.objects.create(
            organisation=self.org, user=self.user, title="Why?", origin=self.origin
        )
        Message.objects.create(
            conversation=self.conversation,
            role=Message.Role.USER,
            content="Why?",
            context={**self.origin, "focus": None},
        )
        self.client.force_authenticate(self.user)

    def test_the_list_carries_origin(self):
        response = self.client.get("/api/v1/copilot/conversations/")
        self.assertEqual(response.data[0]["origin"], self.origin)

    def test_the_detail_carries_origin_and_each_turns_context(self):
        response = self.client.get(f"/api/v1/copilot/conversations/{self.conversation.id}/")
        self.assertEqual(response.data["origin"], self.origin)
        self.assertEqual(response.data["messages"][0]["context"], {**self.origin, "focus": None})


class CommunicationsRegressionTests(APITestCase):
    """A send without `context` — Communications and Copilot — behaves exactly
    as before the dashboard: the text prefix, the book summary, the copilot
    purpose, nothing stored about a screen."""

    url = "/api/v1/copilot/messages/"

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.user = User.objects.create_user(
            email="alice@acme.io", password="supersecret1", name="Alice", organisation=self.org
        )
        Customer.objects.create(organisation=self.org, name="Globex", owner=self.user)
        self.client.force_authenticate(self.user)

    @patch("services.copilot.views.get_completion")
    def test_a_prefixed_send_without_context(self, mock_get_completion):
        mock_get_completion.return_value = "All quiet."

        response = self.client.post(
            self.url, {"content": "[About: Globex] What changed?"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        kwargs = mock_get_completion.call_args.kwargs
        self.assertEqual(kwargs["purpose"], "copilot")
        self.assertIn("Real-data summary", kwargs["system"])
        self.assertNotIn("Screen:", kwargs["system"])
        self.assertEqual(kwargs["messages"][-1]["content"], "[About: Globex] What changed?")
        self.assertIsNone(response.data["origin"])
        self.assertIsNone(response.data["messages"][0]["context"])
        self.assertIsNone(Message.objects.get(role=Message.Role.USER).context)
        self.assertIsNone(Conversation.objects.get().origin)
        # `reply_to` is set for every send, dashboard or not.
        self.assertEqual(
            Message.objects.get(role=Message.Role.ASSISTANT).reply_to,
            Message.objects.get(role=Message.Role.USER),
        )
