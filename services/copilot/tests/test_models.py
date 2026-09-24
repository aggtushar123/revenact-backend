"""Unit tier: model defaults, no HTTP."""

from django.db import IntegrityError, transaction
from django.test import TestCase

from services.accounts.models import Organisation, User
from services.copilot.models import (
    Conversation,
    CopilotSession,
    Message,
    SessionInvite,
    SessionParticipant,
)


class ConversationDefaultsTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.user = User.objects.create_user(
            email="alice@acme.io", password="supersecret1", name="Alice", organisation=self.org
        )

    def test_defaults(self):
        conversation = Conversation.objects.create(organisation=self.org, user=self.user)
        self.assertEqual(conversation.title, "New Chat")

    def test_ordered_most_recently_updated_first(self):
        older = Conversation.objects.create(organisation=self.org, user=self.user, title="Older")
        newer = Conversation.objects.create(organisation=self.org, user=self.user, title="Newer")
        self.assertEqual(list(Conversation.objects.all()), [newer, older])


class MessageDefaultsTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.user = User.objects.create_user(
            email="alice@acme.io", password="supersecret1", name="Alice", organisation=self.org
        )
        self.conversation = Conversation.objects.create(organisation=self.org, user=self.user)

    def test_ordered_oldest_first(self):
        first = Message.objects.create(
            conversation=self.conversation, role=Message.Role.USER, content="Hi"
        )
        second = Message.objects.create(
            conversation=self.conversation, role=Message.Role.ASSISTANT, content="Hello!"
        )
        self.assertEqual(list(self.conversation.messages.all()), [first, second])

    def test_cascade_deletes_with_conversation(self):
        Message.objects.create(conversation=self.conversation, role=Message.Role.USER, content="Hi")
        self.conversation.delete()
        self.assertEqual(Message.objects.count(), 0)


class CopilotSessionDefaultsTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.user = User.objects.create_user(
            email="alice@acme.io", password="supersecret1", name="Alice", organisation=self.org
        )
        self.conversation = Conversation.objects.create(organisation=self.org, user=self.user)

    def test_defaults_to_private_with_no_company_context(self):
        session = CopilotSession.objects.create(conversation=self.conversation)
        self.assertEqual(session.status, CopilotSession.Status.PRIVATE)
        self.assertIsNone(session.customer_id)
        self.assertIsNone(session.account_id)
        self.assertIsNone(session.closed_at)

    def test_only_one_session_per_conversation(self):
        CopilotSession.objects.create(conversation=self.conversation)
        with transaction.atomic(), self.assertRaises(IntegrityError):
            CopilotSession.objects.create(conversation=self.conversation)


class SessionInviteAndParticipantConstraintTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.owner = User.objects.create_user(
            email="alice@acme.io", password="supersecret1", name="Alice", organisation=self.org
        )
        self.teammate = User.objects.create_user(
            email="bob@acme.io", password="supersecret1", name="Bob", organisation=self.org
        )
        conversation = Conversation.objects.create(organisation=self.org, user=self.owner)
        self.session = CopilotSession.objects.create(conversation=conversation)

    def test_cant_double_invite_the_same_user_to_the_same_session(self):
        SessionInvite.objects.create(
            session=self.session, invited_user=self.teammate, invited_by=self.owner
        )
        with transaction.atomic(), self.assertRaises(IntegrityError):
            SessionInvite.objects.create(
                session=self.session, invited_user=self.teammate, invited_by=self.owner
            )

    def test_cant_double_participant_row_the_same_user_in_the_same_session(self):
        SessionParticipant.objects.create(session=self.session, user=self.teammate)
        with transaction.atomic(), self.assertRaises(IntegrityError):
            SessionParticipant.objects.create(session=self.session, user=self.teammate)


class DashboardFieldsTests(TestCase):
    def test_both_are_null_by_default(self):
        org = Organisation.objects.create(name="Acme Inc")
        user = User.objects.create_user(
            email="alice@acme.io", password="supersecret1", name="Alice", organisation=org
        )
        conversation = Conversation.objects.create(organisation=org, user=user)
        message = Message.objects.create(
            conversation=conversation, role=Message.Role.USER, content="Hi"
        )

        self.assertIsNone(conversation.origin)
        self.assertIsNone(message.context)
