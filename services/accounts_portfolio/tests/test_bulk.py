from unittest.mock import patch

from django.db import DatabaseError, connection
from django.test import SimpleTestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from core.models import AuditEvent
from services.accounts.models import User
from services.accounts_portfolio import bulk as bulk_module
from services.accounts_portfolio.serializers import BulkRequestSerializer
from services.accounts_portfolio.tests.fixtures import AccountPortfolioFixture
from services.customers.models import Account
from services.customers.tests.test_views import blind_to_one_account
from services.knowledge.models import Contribution
from services.notifications.models import Notification

URL = "/api/v1/accounts/bulk/"
NOT_YOURS = "can reassign this account"


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
        data = self.validated({"ids": [3, 1, 3, 2, 1], "action": "set_lifecycle", "value": "live"})
        self.assertEqual(data["ids"], [3, 1, 2])

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

    def test_every_stage_is_a_stage_churn_included(self):
        self.assertEqual(
            self.validated({"ids": [1], "action": "set_lifecycle", "value": "churn"})["value"],
            "churn",
        )
        self.assertIn(
            "value", self.errors({"ids": [1], "action": "set_lifecycle", "value": "bogus"})
        )

    def test_there_is_no_archive_or_churn_action(self):
        self.assertIn("action", self.errors({"ids": [1], "action": "archive"}))
        self.assertIn("action", self.errors({"ids": [1], "action": "churn"}))

    def test_ids_are_bounded(self):
        body = {"action": "set_lifecycle", "value": "live"}
        self.assertIn("ids", self.errors({**body, "ids": []}))
        self.assertIn("ids", self.errors({**body, "ids": list(range(1, 502))}))
        self.assertIn("ids", self.errors({**body, "ids": [0]}))


class BulkTests(AccountPortfolioFixture):
    def post(self, body, user=None):
        api = APIClient()
        api.force_authenticate(user or self.csm)
        return api.post(URL, body, format="json")

    def test_requires_authentication(self):
        self.assertEqual(APIClient().post(URL, {}, format="json").status_code, 401)

    def test_set_lifecycle(self):
        a, b = self.account("A"), self.account("B")
        response = self.post({"ids": [a.pk, b.pk], "action": "set_lifecycle", "value": "expansion"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"updated": [a.pk, b.pk], "failed": []})
        stages = set(
            Account.objects.filter(pk__in=[a.pk, b.pk]).values_list("lifecycle_stage", flat=True)
        )
        self.assertEqual(stages, {"expansion"})

    def test_request_validation(self):
        self.assertEqual(
            self.post({"ids": [], "action": "set_lifecycle", "value": "live"}).status_code, 400
        )
        self.assertEqual(self.post({"ids": [1], "action": "archive"}).status_code, 400)
        self.assertEqual(
            self.post({"ids": [1], "action": "set_owner", "value": "carl"}).status_code, 400
        )

    def test_set_owner_hands_over_like_the_detail_view(self):
        a = self.account("A", customers=[self.pizza, self.taco])
        response = self.post({"ids": [a.pk], "action": "set_owner", "value": self.other.pk})
        self.assertEqual(response.data, {"updated": [a.pk], "failed": []})
        self.assertEqual(Account.objects.get(pk=a.pk).owner, self.other)
        # The handover is written on every organisation the account is on.
        for customer in (self.pizza, self.taco):
            self.assertTrue(
                Contribution.objects.filter(customer=customer, author=self.csm).exists()
            )
        notice = Notification.objects.get(
            recipient=self.other, kind=Notification.Kind.ACCOUNT_ASSIGNED
        )
        self.assertEqual(notice.link, f"/accounts/{a.pk}")

    def test_unassigning(self):
        a = self.account("A")
        response = self.post({"ids": [a.pk], "action": "set_owner", "value": None})
        self.assertEqual(response.data["updated"], [a.pk])
        self.assertIsNone(Account.objects.get(pk=a.pk).owner)

    def test_partial_failures_are_reported_per_account(self):
        mine = self.account("Mine")
        danas = self.account("Dana's", customers=[self.taco], owner=self.other)
        globex = self.account("Globex's", customers=[self.globex], owner=None)
        # Visible to Carl through Pizza Hut, which he owns, but only Dana, her
        # managers or a settings manager may reassign it.
        shared = self.account("Shared", owner=self.other)
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
        self.assertIn(NOT_YOURS, failed[shared.pk])
        self.assertEqual(Account.objects.get(pk=shared.pk).owner, self.other)

        response = self.post({"ids": [mine.pk], "action": "set_owner", "value": outsider.pk})
        self.assertEqual(
            response.data["failed"],
            [{"id": mine.pk, "reason": "Owner must be a member of your own organisation."}],
        )

    def test_blind_to_one_account(self):
        viewer, seen, hidden = blind_to_one_account(self.pizza)
        response = self.post(
            {"ids": [seen.pk, hidden.pk], "action": "set_lifecycle", "value": "live"}, user=viewer
        )
        self.assertEqual(response.data["updated"], [seen.pk])
        self.assertEqual(response.data["failed"], [{"id": hidden.pk, "reason": "Not found."}])
        self.assertNotEqual(Account.objects.get(pk=hidden.pk).lifecycle_stage, "live")

    def test_admin_may_reassign_anyones(self):
        danas = self.account("Dana's", customers=[self.taco], owner=self.other)
        response = self.post(
            {"ids": [danas.pk], "action": "set_owner", "value": self.csm.pk}, user=self.admin
        )
        self.assertEqual(response.data["updated"], [danas.pk])

    def test_an_inactive_owner_is_refused(self):
        a = self.account("A")
        self.other.is_active = False
        self.other.save(update_fields=["is_active"])
        response = self.post({"ids": [a.pk], "action": "set_owner", "value": self.other.pk})
        self.assertEqual(
            response.data["failed"],
            [{"id": a.pk, "reason": "Owner must be an active member of your organisation."}],
        )
        self.assertEqual(Account.objects.get(pk=a.pk).owner, self.csm)

    def test_the_single_edit_refuses_an_inactive_owner_too(self):
        a = self.account("A")
        self.other.is_active = False
        self.other.save(update_fields=["is_active"])
        api = APIClient()
        api.force_authenticate(self.csm)
        response = api.patch(
            f"/api/v1/customers/{self.pizza.pk}/accounts/{a.pk}/",
            {"owner_id": self.other.pk},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["owner_id"], ["Owner must be an active member of your organisation."]
        )

    def test_duplicate_ids_apply_once(self):
        a = self.account("A")
        response = self.post({"ids": [a.pk, a.pk], "action": "set_lifecycle", "value": "live"})
        self.assertEqual(response.data["updated"], [a.pk])

    def test_audited_with_ids_and_action(self):
        a = self.account("A")
        danas = self.account("Dana's", customers=[self.taco], owner=self.other)
        self.post({"ids": [a.pk, danas.pk], "action": "set_lifecycle", "value": "live"})
        event = AuditEvent.objects.get(action="accounts.bulk_updated")
        self.assertEqual(event.outcome, AuditEvent.Outcome.SUCCESS)
        self.assertEqual(
            event.metadata,
            {"action": "set_lifecycle", "value": "live", "ids": [a.pk], "failed_ids": [danas.pk]},
        )

    def test_nothing_updated_is_a_failure_outcome(self):
        danas = self.account("Dana's", customers=[self.taco], owner=self.other)
        self.post({"ids": [danas.pk], "action": "set_lifecycle", "value": "live"})
        event = AuditEvent.objects.get(action="accounts.bulk_updated")
        self.assertEqual(event.outcome, AuditEvent.Outcome.FAILURE)

    def test_a_database_error_fails_only_that_id(self):
        a, b, c = self.account("A"), self.account("B"), self.account("C")
        real_save = Account.save

        def save(instance, *args, **kwargs):
            if instance.pk == b.pk:
                raise DatabaseError("disk on fire")
            return real_save(instance, *args, **kwargs)

        with (
            patch.object(Account, "save", save),
            self.assertLogs("services.accounts_portfolio.bulk", level="WARNING") as logs,
        ):
            response = self.post(
                {"ids": [a.pk, b.pk, c.pk], "action": "set_lifecycle", "value": "live"}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["updated"], [a.pk, c.pk])
        self.assertEqual(response.data["failed"], [{"id": b.pk, "reason": "Could not be updated."}])
        self.assertNotEqual(Account.objects.get(pk=b.pk).lifecycle_stage, "live")
        self.assertNotIn("disk on fire", "\n".join(logs.output))
        event = AuditEvent.objects.get(action="accounts.bulk_updated")
        self.assertEqual(
            (event.metadata["ids"], event.metadata["failed_ids"]), ([a.pk, c.pk], [b.pk])
        )

    def test_an_unexpected_error_still_audits_what_was_done(self):
        a, b = self.account("A"), self.account("B")
        api = APIClient(raise_request_exception=False)
        api.force_authenticate(self.csm)
        with patch(
            "services.accounts_portfolio.bulk.after_account_update",
            side_effect=[None, RuntimeError("boom")],
        ):
            response = api.post(
                URL,
                {"ids": [a.pk, b.pk], "action": "set_lifecycle", "value": "live"},
                format="json",
            )
        self.assertEqual(response.status_code, 500)
        self.assertEqual(Account.objects.get(pk=a.pk).lifecycle_stage, "live")
        self.assertNotEqual(Account.objects.get(pk=b.pk).lifecycle_stage, "live")
        event = AuditEvent.objects.get(action="accounts.bulk_updated")
        self.assertEqual(event.outcome, AuditEvent.Outcome.FAILURE)
        self.assertEqual(event.metadata["ids"], [a.pk])

    def test_each_row_is_locked_and_read_fresh(self):
        a = self.account("A")
        with CaptureQueriesContext(connection) as queries:
            self.post({"ids": [a.pk], "action": "set_lifecycle", "value": "live"})
        locked = [q["sql"] for q in queries if "FOR UPDATE" in q["sql"]]
        self.assertEqual(len(locked), 1)
        self.assertIn('"customers_account"', locked[0])

    def test_authorisation_sees_the_current_owner(self):
        # B is Carl's to see through Pizza Hut. While the batch is on A, Dana
        # takes B over; by B's turn only Dana's chain or a settings manager
        # may reassign it, so a snapshot from the start must not do.
        a, b = self.account("A"), self.account("B")
        real_after = bulk_module.after_account_update

        def after(account, **kwargs):
            if account.pk == a.pk:
                Account.objects.filter(pk=b.pk).update(owner=self.other)
            return real_after(account, **kwargs)

        with patch("services.accounts_portfolio.bulk.after_account_update", after):
            response = self.post({"ids": [a.pk, b.pk], "action": "set_owner", "value": None})
        self.assertEqual(response.data["updated"], [a.pk])
        self.assertIn(NOT_YOURS, response.data["failed"][0]["reason"])
        self.assertEqual(Account.objects.get(pk=b.pk).owner, self.other)
