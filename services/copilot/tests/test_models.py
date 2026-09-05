"""Unit tier: model defaults, no HTTP."""

from django.test import TestCase

from services.accounts.models import Organisation, User
from services.copilot.models import Conversation, Message


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
