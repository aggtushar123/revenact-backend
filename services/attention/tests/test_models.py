from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase

from services.accounts.models import Organisation
from services.attention.models import AttentionSnooze

User = get_user_model()


class AttentionSnoozeTestCase(TestCase):
    """Test AttentionSnooze model constraints and behavior."""

    def setUp(self):
        """Create test organisation and users."""
        self.org = Organisation.objects.create(name="Test Org")
        self.user1 = User.objects.create(email="user1@example.com")
        self.user2 = User.objects.create(email="user2@example.com")

    def test_same_user_and_key_raises_integrity_error(self):
        """Two snoozes with the same user and key should raise IntegrityError."""
        AttentionSnooze.objects.create(
            organisation=self.org,
            user=self.user1,
            key="test_key",
        )
        with self.assertRaises(IntegrityError):
            AttentionSnooze.objects.create(
                organisation=self.org,
                user=self.user1,
                key="test_key",
            )

    def test_same_key_different_users_is_allowed(self):
        """The same key for two different users should be allowed."""
        snooze1 = AttentionSnooze.objects.create(
            organisation=self.org,
            user=self.user1,
            key="test_key",
        )
        snooze2 = AttentionSnooze.objects.create(
            organisation=self.org,
            user=self.user2,
            key="test_key",
        )
        self.assertEqual(snooze1.user_id, self.user1.id)
        self.assertEqual(snooze2.user_id, self.user2.id)
        self.assertEqual(snooze1.key, snooze2.key)

    def test_str_representation(self):
        """Test the __str__ method returns user_id:key."""
        snooze = AttentionSnooze.objects.create(
            organisation=self.org,
            user=self.user1,
            key="test_key",
        )
        self.assertEqual(str(snooze), f"{self.user1.id}:test_key")
