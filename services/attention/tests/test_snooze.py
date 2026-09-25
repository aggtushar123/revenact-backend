"""Pruning old snoozes out of the table — separate from `visible_items`
above, which already treats an expired snooze as simply not active. This
is table hygiene: a snooze expired long enough ago that nobody will ever
look at it again gets deleted outright. `until=null` (Done) is never
pruned — it isn't expired, it's permanent."""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from services.accounts.models import Organisation, User
from services.attention.models import AttentionSnooze
from services.attention.snooze import prune_expired


class PruneExpiredTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.user = User.objects.create_user(
            email="carl@acme.io", password="supersecret1", name="Carl", organisation=self.org
        )
        self.now = timezone.now()

    def snooze(self, key, **fields):
        return AttentionSnooze.objects.create(
            organisation=self.org, user=self.user, key=key, **fields
        )

    def test_deletes_a_snooze_expired_over_a_month_ago(self):
        self.snooze("renewal:1", until=self.now - timedelta(days=31))

        count = prune_expired(now=self.now)

        self.assertEqual(count, 1)
        self.assertFalse(AttentionSnooze.objects.filter(key="renewal:1").exists())

    def test_keeps_a_snooze_expired_less_than_a_month_ago(self):
        self.snooze("renewal:2", until=self.now - timedelta(days=10))

        count = prune_expired(now=self.now)

        self.assertEqual(count, 0)
        self.assertTrue(AttentionSnooze.objects.filter(key="renewal:2").exists())

    def test_keeps_a_done_snooze_forever(self):
        self.snooze("renewal:3", until=None)

        count = prune_expired(now=self.now)

        self.assertEqual(count, 0)
        self.assertTrue(AttentionSnooze.objects.filter(key="renewal:3").exists())

    def test_keeps_a_still_active_snooze(self):
        self.snooze("renewal:4", until=self.now + timedelta(days=5))

        count = prune_expired(now=self.now)

        self.assertEqual(count, 0)
        self.assertTrue(AttentionSnooze.objects.filter(key="renewal:4").exists())

    def test_dry_run_reports_without_deleting(self):
        self.snooze("renewal:5", until=self.now - timedelta(days=45))

        count = prune_expired(now=self.now, dry_run=True)

        self.assertEqual(count, 1)
        self.assertTrue(AttentionSnooze.objects.filter(key="renewal:5").exists())
