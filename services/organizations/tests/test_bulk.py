from unittest.mock import patch

from django.db import DatabaseError, connection
from django.test import SimpleTestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from core.models import AuditEvent
from services.accounts.models import User
from services.customers.models import Account, Customer
from services.knowledge.models import Contribution
from services.organizations import bulk as bulk_module
from services.organizations.serializers import BulkRequestSerializer
from services.organizations.tests.fixtures import PortfolioFixture

URL = "/api/v1/organizations/bulk/"


class BulkRequestSerializerTests(SimpleTestCase):
    def validated(self, body):
        serializer = BulkRequestSerializer(data=body)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        return serializer.validated_data

    def errors(self, body):
        serializer = BulkRequestSerializer(data=body)
        self.assertFalse(serializer.is_valid())
        return serializer.errors

    def test_ids_are_deduplicated_in_order(self):
        data = self.validated({"ids": [3, 1, 3, 2, 1], "action": "archive"})
        self.assertEqual(data["ids"], [3, 1, 2])

    def test_archive_always_means_true(self):
        self.assertIs(self.validated({"ids": [1], "action": "archive"})["value"], True)
        data = self.validated({"ids": [1], "action": "archive", "value": False})
        self.assertIs(data["value"], True)

    def test_owner_is_an_id_or_an_explicit_null(self):
        self.assertEqual(
            self.validated({"ids": [1], "action": "set_owner", "value": 7})["value"], 7
        )
        self.assertIsNone(
            self.validated({"ids": [1], "action": "set_owner", "value": None})["value"]
        )
        for bad in (True, 1.5, "7"):
            self.assertIn("value", self.errors({"ids": [1], "action": "set_owner", "value": bad}))

    def test_a_missing_owner_does_not_mean_unassign(self):
        self.assertIn("value", self.errors({"ids": [1], "action": "set_owner"}))

    def test_ids_are_bounded(self):
        self.assertIn("ids", self.errors({"ids": [], "action": "archive"}))
        self.assertIn("ids", self.errors({"ids": list(range(1, 502)), "action": "archive"}))
        self.assertIn("ids", self.errors({"ids": [0], "action": "archive"}))


class BulkTests(PortfolioFixture):
    def post(self, body, user=None):
        api = APIClient()
        api.force_authenticate(user or self.csm)
        return api.post(URL, body, format="json")

    def test_requires_authentication(self):
        self.assertEqual(APIClient().post(URL, {}, format="json").status_code, 401)

    def test_set_lifecycle(self):
        a, b = self.customer("A"), self.customer("B")
        response = self.post({"ids": [a.pk, b.pk], "action": "set_lifecycle", "value": "expansion"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"updated": [a.pk, b.pk], "failed": []})
        stages = set(
            Customer.objects.filter(pk__in=[a.pk, b.pk]).values_list("lifecycle_stage", flat=True)
        )
        self.assertEqual(stages, {"expansion"})
        self.assertEqual(Customer.objects.get(pk=a.pk).modified_by, self.csm)

    def test_churn_is_not_a_bulk_stage(self):
        a = self.customer("A")
        response = self.post({"ids": [a.pk], "action": "set_lifecycle", "value": "churn"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("value", response.data)
        response = self.post({"ids": [a.pk], "action": "set_lifecycle", "value": "bogus"})
        self.assertEqual(response.status_code, 400)

    def test_request_validation(self):
        self.assertEqual(self.post({"ids": [], "action": "archive"}).status_code, 400)
        self.assertEqual(
            self.post({"ids": list(range(1, 502)), "action": "archive"}).status_code, 400
        )
        self.assertEqual(self.post({"ids": [1], "action": "delete"}).status_code, 400)
        self.assertEqual(
            self.post({"ids": [1], "action": "set_owner", "value": "carl"}).status_code, 400
        )

    def test_archive_hides_from_the_portfolio(self):
        a = self.customer("A")
        response = self.post({"ids": [a.pk], "action": "archive"})
        self.assertEqual(response.data, {"updated": [a.pk], "failed": []})
        self.assertTrue(Customer.objects.get(pk=a.pk).is_archived)
        api = APIClient()
        api.force_authenticate(self.csm)
        self.assertEqual(api.get("/api/v1/organizations/portfolio/").data["count"], 0)

    def test_set_owner_hands_over_like_the_detail_view(self):
        a = self.customer("A")
        response = self.post({"ids": [a.pk], "action": "set_owner", "value": self.other.pk})
        self.assertEqual(response.data, {"updated": [a.pk], "failed": []})
        self.assertEqual(Customer.objects.get(pk=a.pk).owner, self.other)
        self.assertTrue(Contribution.objects.filter(customer=a, author=self.csm).exists())

    def test_unassigning(self):
        a = self.customer("A")
        response = self.post({"ids": [a.pk], "action": "set_owner", "value": None})
        self.assertEqual(response.data["updated"], [a.pk])
        self.assertIsNone(Customer.objects.get(pk=a.pk).owner)

    def test_partial_failures_are_reported_per_account(self):
        mine = self.customer("Mine")
        danas = self.customer("Dana's", owner=self.other)
        globex = self.customer("Globex's", owner=None, organisation=self.other_org)
        # Visible to Carl through an account he owns, but only Dana, her
        # managers or a settings manager may reassign it.
        shared = self.customer("Shared", owner=self.other)
        division = Account.objects.create(name="Shared EMEA", owner=self.csm)
        division.customers.add(shared)
        outsider = User.objects.create_user(
            email="olga@globex.io",
            password="supersecret1",
            name="Olga",
            organisation=self.other_org,
            role=User.Role.CSM,
        )

        response = self.post(
            {
                "ids": [mine.pk, danas.pk, globex.pk, 999999, shared.pk],
                "action": "set_owner",
                "value": self.csm.pk,
            }
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["updated"], [mine.pk])
        failed = {row["id"]: row["reason"] for row in response.data["failed"]}
        self.assertEqual(failed[danas.pk], "Not found.")
        self.assertEqual(failed[globex.pk], "Not found.")
        self.assertEqual(failed[999999], "Not found.")
        self.assertIn("can reassign this account", failed[shared.pk])
        self.assertEqual(Customer.objects.get(pk=shared.pk).owner, self.other)

        response = self.post({"ids": [mine.pk], "action": "set_owner", "value": outsider.pk})
        self.assertEqual(
            response.data["failed"],
            [{"id": mine.pk, "reason": "Owner must be a member of your own organisation."}],
        )

    def test_admin_may_reassign_anyones(self):
        danas = self.customer("Dana's", owner=self.other)
        response = self.post(
            {"ids": [danas.pk], "action": "set_owner", "value": self.csm.pk}, user=self.admin
        )
        self.assertEqual(response.data["updated"], [danas.pk])

    def test_duplicate_ids_apply_once(self):
        a = self.customer("A")
        response = self.post({"ids": [a.pk, a.pk], "action": "archive"})
        self.assertEqual(response.data["updated"], [a.pk])

    def test_audited_with_ids_and_action(self):
        a = self.customer("A")
        danas = self.customer("Dana's", owner=self.other)
        self.post({"ids": [a.pk, danas.pk], "action": "set_lifecycle", "value": "live"})
        event = AuditEvent.objects.get(action="organizations.bulk_updated")
        self.assertEqual(event.outcome, AuditEvent.Outcome.SUCCESS)
        self.assertEqual(
            event.metadata,
            {"action": "set_lifecycle", "value": "live", "ids": [a.pk], "failed_ids": [danas.pk]},
        )

    def test_nothing_updated_is_a_failure_outcome(self):
        danas = self.customer("Dana's", owner=self.other)
        self.post({"ids": [danas.pk], "action": "archive"})
        event = AuditEvent.objects.get(action="organizations.bulk_updated")
        self.assertEqual(event.outcome, AuditEvent.Outcome.FAILURE)

    def test_a_database_error_fails_only_that_id(self):
        a, b, c = self.customer("A"), self.customer("B"), self.customer("C")
        real_save = Customer.save

        def save(instance, *args, **kwargs):
            if instance.pk == b.pk:
                raise DatabaseError("disk on fire")
            return real_save(instance, *args, **kwargs)

        with (
            patch.object(Customer, "save", save),
            self.assertLogs("services.organizations.bulk", level="WARNING") as logs,
        ):
            response = self.post({"ids": [a.pk, b.pk, c.pk], "action": "archive"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["updated"], [a.pk, c.pk])
        self.assertEqual(response.data["failed"], [{"id": b.pk, "reason": "Could not be updated."}])
        self.assertFalse(Customer.objects.get(pk=b.pk).is_archived)
        self.assertNotIn("disk on fire", "\n".join(logs.output))
        event = AuditEvent.objects.get(action="organizations.bulk_updated")
        self.assertEqual(event.metadata["ids"], [a.pk, c.pk])
        self.assertEqual(event.metadata["failed_ids"], [b.pk])

    def test_an_unexpected_error_still_audits_what_was_done(self):
        a, b = self.customer("A"), self.customer("B")
        api = APIClient(raise_request_exception=False)
        api.force_authenticate(self.csm)
        with patch(
            "services.organizations.bulk.after_customer_update",
            side_effect=[None, RuntimeError("boom")],
        ):
            response = api.post(URL, {"ids": [a.pk, b.pk], "action": "archive"}, format="json")
        self.assertEqual(response.status_code, 500)
        self.assertTrue(Customer.objects.get(pk=a.pk).is_archived)
        self.assertFalse(Customer.objects.get(pk=b.pk).is_archived)
        event = AuditEvent.objects.get(action="organizations.bulk_updated")
        self.assertEqual(event.outcome, AuditEvent.Outcome.FAILURE)
        self.assertEqual(event.metadata["ids"], [a.pk])

    def test_an_inactive_owner_is_refused(self):
        a = self.customer("A")
        self.other.is_active = False
        self.other.save(update_fields=["is_active"])
        response = self.post({"ids": [a.pk], "action": "set_owner", "value": self.other.pk})
        self.assertEqual(
            response.data["failed"],
            [{"id": a.pk, "reason": "Owner must be an active member of your organisation."}],
        )
        self.assertEqual(Customer.objects.get(pk=a.pk).owner, self.csm)

    def test_each_row_is_locked_and_read_fresh(self):
        a = self.customer("A")
        with CaptureQueriesContext(connection) as queries:
            self.post({"ids": [a.pk], "action": "archive"})
        locked = [q["sql"] for q in queries if "FOR UPDATE" in q["sql"]]
        self.assertEqual(len(locked), 1)
        self.assertIn('"customers_customer"', locked[0])

    def test_authorisation_sees_the_current_owner(self):
        # B is visible to Carl through an account he owns. While the batch is
        # on A, Dana takes B over; by B's turn only Dana's chain or a settings
        # manager may reassign it, so a snapshot from the start must not do.
        a, b = self.customer("A"), self.customer("B")
        division = Account.objects.create(name="B EMEA", owner=self.csm)
        division.customers.add(b)
        real_after = bulk_module.after_customer_update

        def after(customer, **kwargs):
            if customer.pk == a.pk:
                Customer.objects.filter(pk=b.pk).update(owner=self.other)
            return real_after(customer, **kwargs)

        with patch("services.organizations.bulk.after_customer_update", after):
            response = self.post({"ids": [a.pk, b.pk], "action": "set_owner", "value": None})
        self.assertEqual(response.data["updated"], [a.pk])
        self.assertIn("can reassign this account", response.data["failed"][0]["reason"])
        self.assertEqual(Customer.objects.get(pk=b.pk).owner, self.other)


class ArchiveGateTests(PortfolioFixture):
    """Archiving, either way, is allowed only to whoever may reassign the
    customer: its owner, their management chain or a settings manager — or
    anyone, when nobody owns it. The single PATCH and bulk share the rule."""

    def setUp(self):
        super().setUp()
        # Visible to Carl through an account he owns, but Dana's to reassign.
        self.shared = self.customer("Shared", owner=self.other)
        division = Account.objects.create(name="Shared EMEA", owner=self.csm)
        division.customers.add(self.shared)
        self.mine = self.customer("Mine")
        self.pool = self.customer("Pool", owner=None)

    post = BulkTests.post

    def patch(self, customer, value, user=None):
        api = APIClient()
        api.force_authenticate(user or self.csm)
        return api.patch(f"/api/v1/customers/{customer.pk}/", {"is_archived": value}, format="json")

    def test_a_csm_may_not_archive_what_they_may_not_reassign(self):
        response = self.patch(self.shared, True)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["is_archived"], ["You can't archive this organization."])
        self.assertFalse(Customer.objects.get(pk=self.shared.pk).is_archived)

    def test_nor_restore_it(self):
        Customer.objects.filter(pk=self.shared.pk).update(is_archived=True)
        response = self.patch(self.shared, False)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["is_archived"], ["You can't restore this organization."])
        self.assertTrue(Customer.objects.get(pk=self.shared.pk).is_archived)

    def test_their_own_and_the_pool_may_be_archived_and_restored(self):
        for customer in (self.mine, self.pool):
            with self.subTest(customer=customer.name):
                self.assertEqual(self.patch(customer, True).status_code, 200)
                self.assertTrue(Customer.objects.get(pk=customer.pk).is_archived)
                self.assertEqual(self.patch(customer, False).status_code, 200)
                self.assertFalse(Customer.objects.get(pk=customer.pk).is_archived)

    def test_the_owners_chain_and_a_settings_manager_may(self):
        self.assertEqual(self.patch(self.shared, True, user=self.admin).status_code, 200)

    def test_an_unchanged_flag_is_not_judged(self):
        """A form that re-sends the whole record must not fail on a field the
        caller did not change."""
        self.assertEqual(self.patch(self.shared, False).status_code, 200)

    def test_bulk_archive_with_a_mixed_selection(self):
        response = self.post(
            {"ids": [self.mine.pk, self.shared.pk, self.pool.pk], "action": "archive"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["updated"], [self.mine.pk, self.pool.pk])
        self.assertEqual(
            response.data["failed"],
            [{"id": self.shared.pk, "reason": "You can't archive this organization."}],
        )
        self.assertFalse(Customer.objects.get(pk=self.shared.pk).is_archived)
