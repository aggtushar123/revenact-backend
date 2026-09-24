"""The Dashboard Overview's "Needs attention" endpoints: the list itself and
per-user snoozing.

Fixtures deliberately build only renewal items (one rule is enough to test
sorting, capping, scoping and snoozing) — `test_rules.py` already covers
every kind's own shape.
"""

from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import AuditEvent
from services.accounts.models import Organisation, User
from services.attention import rules
from services.attention.models import AttentionSnooze
from services.customers.models import Customer

POOR = Decimal("2.0")


class AttentionViewTests(APITestCase):
    url = "/api/v1/dashboard/attention/"
    snooze_url = "/api/v1/dashboard/attention/snooze/"

    def setUp(self):
        self.today = timezone.localdate()
        self.org = Organisation.objects.create(name="Acme Inc", currency="USD")
        self.csm = User.objects.create_user(
            email="carl@acme.io",
            password="supersecret1",
            name="Carl",
            organisation=self.org,
            role=User.Role.CSM,
            function=User.Function.CS,
        )
        self.other = User.objects.create_user(
            email="dana@acme.io",
            password="supersecret1",
            name="Dana",
            organisation=self.org,
            role=User.Role.CSM,
            function=User.Function.CS,
        )
        self.client.force_authenticate(self.csm)

    def _renewal_customer(self, name, *, days=5, arr=100_000, owner=None):
        """A renewal item: overdue by `days`, Poor health, unowned so every
        member of the org can see it (the visibility rule everyone-sees-an-
        unowned-record) — the tests here are about scoring/snoozing, not
        `services.customers.scoping`, which `test_rules.py` already covers."""
        return Customer.objects.create(
            organisation=self.org,
            name=name,
            owner=owner,
            health_score=POOR,
            arr_billed_at_account=arr,
            currency="USD",
            renewal_date=self.today - timedelta(days=days),
        )

    def _key(self, customer):
        return f"renewal:{customer.pk}"

    def _snooze(self, key, **body):
        return self.client.post(self.snooze_url, {"key": key, **body}, format="json")

    def _delete(self, key):
        return self.client.delete(f"{self.snooze_url}{key}/")

    # ── the list endpoint ────────────────────────────────────────────

    def test_unauthenticated_is_refused(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_response_has_currency_and_filters(self):
        self._renewal_customer("Acme")

        data = self.client.get(self.url).data

        self.assertEqual(data["currency"], "USD")
        self.assertEqual(set(data["filters"]), {"owners", "lifecycles", "customers"})

    def test_items_are_sorted_by_score_desc_and_capped_at_25(self):
        customers = [self._renewal_customer(f"C{i}", arr=(i + 1) * 10_000) for i in range(26)]

        items = self.client.get(self.url).data["items"]

        self.assertEqual(len(items), 25)
        scores = [item["score"] for item in items]
        self.assertEqual(scores, sorted(scores, reverse=True))
        # The smallest ARR customer (score-lowest) is the one left out.
        smallest_key = self._key(customers[0])
        self.assertNotIn(smallest_key, {item["key"] for item in items})
        largest_key = self._key(customers[-1])
        self.assertEqual(items[0]["key"], largest_key)

    # ── snoozing ─────────────────────────────────────────────────────

    def test_snoozing_for_seven_days_hides_the_item_for_that_user_only(self):
        customer = self._renewal_customer("Acme")
        key = self._key(customer)

        response = self._snooze(key, days=7)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["key"], key)
        self.assertIsNotNone(response.data["until"])

        keys = {item["key"] for item in self.client.get(self.url).data["items"]}
        self.assertNotIn(key, keys)

        self.client.force_authenticate(self.other)
        other_keys = {item["key"] for item in self.client.get(self.url).data["items"]}
        self.assertIn(key, other_keys)

    def test_done_hides_the_item(self):
        customer = self._renewal_customer("Acme")
        key = self._key(customer)

        response = self._snooze(key, done=True)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(response.data["until"])
        keys = {item["key"] for item in self.client.get(self.url).data["items"]}
        self.assertNotIn(key, keys)

    def test_a_snoozed_item_that_got_worse_shows_again(self):
        # Renews in 5 days: not yet overdue.
        customer = self._renewal_customer("Acme", days=-5)
        key = self._key(customer)
        self._snooze(key, days=7)
        self.assertNotIn(key, {item["key"] for item in self.client.get(self.url).data["items"]})

        # Now overdue, which it wasn't when it was snoozed.
        customer.renewal_date = self.today - timedelta(days=20)
        customer.save(update_fields=["renewal_date"])

        keys = {item["key"] for item in self.client.get(self.url).data["items"]}
        self.assertIn(key, keys)

    def test_an_expired_snooze_shows_the_item_again(self):
        customer = self._renewal_customer("Acme")
        key = self._key(customer)
        AttentionSnooze.objects.create(
            organisation=self.org,
            user=self.csm,
            key=key,
            until=timezone.now() - timedelta(days=1),
            fingerprint={"overdue": True, "health": "poor", "arr": 100_000.0},
        )

        keys = {item["key"] for item in self.client.get(self.url).data["items"]}
        self.assertIn(key, keys)

    def test_snoozing_a_key_not_on_your_list_is_refused(self):
        response = self._snooze("renewal:99999", days=7)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data, {"key": ["Not an item on your list."]})

    def test_delete_restores_the_item(self):
        customer = self._renewal_customer("Acme")
        key = self._key(customer)
        self._snooze(key, days=7)
        self.assertNotIn(key, {item["key"] for item in self.client.get(self.url).data["items"]})

        response = self._delete(key)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(AttentionSnooze.objects.filter(user=self.csm, key=key).exists())
        self.assertIn(key, {item["key"] for item in self.client.get(self.url).data["items"]})

    def test_delete_accepts_a_percent_encoded_key(self):
        # What the browser sends for encodeURIComponent("renewal:<id>").
        customer = self._renewal_customer("Acme")
        key = self._key(customer)
        self._snooze(key, days=7)

        response = self.client.delete(f"{self.snooze_url}renewal%3A{customer.pk}/")

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(AttentionSnooze.objects.filter(user=self.csm, key=key).exists())

    def test_malformed_keys_are_refused_like_any_other_unknown_key(self):
        customer = self._renewal_customer("Acme")
        for key in (
            "renewal",
            "renewal:",
            "renewal:abc",
            f"renewal:{customer.pk}:x",
            f"renewal:0{customer.pk}",
            f"bogus:{customer.pk}",
        ):
            with self.subTest(key=key):
                response = self._snooze(key, days=7)
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(response.data, {"key": ["Not an item on your list."]})
        self.assertFalse(AttentionSnooze.objects.exists())

    def test_snoozing_another_csms_customer_is_refused(self):
        # Scoping the check to one company still goes through the viewer's
        # own visible book.
        customer = self._renewal_customer("Theirs", owner=self.other)

        response = self._snooze(self._key(customer), days=7)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_post_builds_only_the_keys_kind_for_its_one_company(self):
        customer = self._renewal_customer("Acme")
        self._renewal_customer("Other")
        with mock.patch("services.attention.views.build_items", wraps=rules.build_items) as build:
            response = self._snooze(self._key(customer), days=7)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        args, kwargs = build.call_args
        self.assertEqual(args[1], {"customer": str(customer.pk)})
        self.assertEqual(kwargs["kinds"], ("renewal",))

    def test_delete_without_a_snooze_is_404(self):
        response = self._delete("renewal:99999")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_audit_events_carry_the_right_metadata(self):
        customer = self._renewal_customer("Acme")
        key = self._key(customer)

        self._snooze(key, days=7)
        snoozed = AuditEvent.objects.get(action="attention.snoozed")
        self.assertEqual(snoozed.actor_id, self.csm.id)
        self.assertEqual(snoozed.metadata["key"], key)
        self.assertEqual(snoozed.metadata["days"], 7)

        self._delete(key)
        unsnoozed = AuditEvent.objects.get(action="attention.unsnoozed")
        self.assertEqual(unsnoozed.actor_id, self.csm.id)
        self.assertEqual(unsnoozed.metadata["key"], key)

    def test_done_audit_metadata(self):
        customer = self._renewal_customer("Acme")
        key = self._key(customer)

        self._snooze(key, done=True)

        snoozed = AuditEvent.objects.get(action="attention.snoozed")
        self.assertEqual(snoozed.metadata["key"], key)
        self.assertEqual(snoozed.metadata.get("done"), True)

    def test_another_orgs_user_cannot_snooze_your_keys(self):
        customer = self._renewal_customer("Acme")
        key = self._key(customer)
        other_org = Organisation.objects.create(name="Other Inc", currency="USD")
        stranger = User.objects.create_user(
            email="eve@other.io",
            password="supersecret1",
            name="Eve",
            organisation=other_org,
            role=User.Role.CSM,
            function=User.Function.CS,
        )
        self.client.force_authenticate(stranger)

        response = self._snooze(key, days=7)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(AttentionSnooze.objects.filter(key=key).exists())

    def test_snoozing_the_same_key_again_updates_the_one_row(self):
        customer = self._renewal_customer("Acme", days=-5)
        key = self._key(customer)

        first = self._snooze(key, days=7)
        # The item changes before the second snooze, so the refreshed
        # fingerprint is actually a different one to assert against.
        customer.renewal_date = self.today - timedelta(days=20)
        customer.save(update_fields=["renewal_date"])
        second = self._snooze(key, days=30)

        self.assertEqual(second.status_code, status.HTTP_201_CREATED)
        self.assertEqual(AttentionSnooze.objects.filter(user=self.csm, key=key).count(), 1)
        row = AttentionSnooze.objects.get(user=self.csm, key=key)
        self.assertGreater(row.until, first.data["until"])
        self.assertIs(row.fingerprint["overdue"], True)

    # ── one verb per URL ─────────────────────────────────────────────

    def test_post_to_the_detail_url_is_not_allowed(self):
        customer = self._renewal_customer("Acme")
        key = self._key(customer)

        response = self.client.post(f"{self.snooze_url}{key}/", {"days": 7}, format="json")

        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_delete_to_the_collection_url_is_not_allowed(self):
        response = self.client.delete(self.snooze_url)

        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
