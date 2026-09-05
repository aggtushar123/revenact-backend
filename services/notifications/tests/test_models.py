"""Unit tier: model defaults, no HTTP."""

from django.test import TestCase

from services.accounts.models import Organisation, User
from services.notifications.models import Notification


class NotificationDefaultsTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.alice = User.objects.create_user(
            email="alice@acme.io", password="supersecret1", name="Alice", organisation=self.org
        )
        self.carl = User.objects.create_user(
            email="carl@acme.io", password="supersecret1", name="Carl", organisation=self.org
        )

    def test_defaults_to_unread(self):
        notification = Notification.objects.create(
            recipient=self.alice,
            actor=self.carl,
            kind=Notification.Kind.COPILOT_INVITE,
            message="Carl invited you to a live Copilot session",
        )
        self.assertFalse(notification.is_read)
        self.assertEqual(notification.link, "")

    def test_ordered_most_recent_first(self):
        older = Notification.objects.create(
            recipient=self.alice, kind=Notification.Kind.CUSTOMER_ASSIGNED, message="Older"
        )
        newer = Notification.objects.create(
            recipient=self.alice, kind=Notification.Kind.CUSTOMER_ASSIGNED, message="Newer"
        )
        self.assertEqual(list(Notification.objects.all()), [newer, older])

    def test_actor_can_be_null(self):
        notification = Notification.objects.create(
            recipient=self.alice, actor=None, kind=Notification.Kind.CUSTOMER_ASSIGNED, message="x"
        )
        self.assertIsNone(notification.actor)

    def test_deleting_the_actor_leaves_the_notification_with_no_actor(self):
        notification = Notification.objects.create(
            recipient=self.alice,
            actor=self.carl,
            kind=Notification.Kind.CUSTOMER_ASSIGNED,
            message="x",
        )
        self.carl.delete()
        notification.refresh_from_db()
        self.assertIsNone(notification.actor)

    def test_deleting_the_recipient_deletes_the_notification(self):
        Notification.objects.create(
            recipient=self.alice, kind=Notification.Kind.CUSTOMER_ASSIGNED, message="x"
        )
        self.alice.delete()
        self.assertEqual(Notification.objects.count(), 0)
