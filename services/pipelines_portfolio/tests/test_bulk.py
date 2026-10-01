from datetime import timedelta
from unittest.mock import patch

from django.db import DatabaseError, connection
from django.test import SimpleTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import AuditEvent
from services.accounts.models import User
from services.customers.models import Opportunity, Risk
from services.customers.tests.test_views import blind_to_one_account
from services.pipelines_portfolio import bulk as bulk_module
from services.pipelines_portfolio.kinds import OPPORTUNITIES, RISKS
from services.pipelines_portfolio.serializers import BulkRequestSerializer
from services.pipelines_portfolio.tests.fixtures import PipelineFixture

URL = "/api/v1/pipelines/{}/bulk/"
MISSING = 999_999


class BulkRequestSerializerTests(SimpleTestCase):
    def validated(self, body, kind=OPPORTUNITIES):
        serializer = BulkRequestSerializer(data=body, context={"kind": kind})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        return serializer.validated_data

    def errors(self, body, kind=OPPORTUNITIES):
        serializer = BulkRequestSerializer(data=body, context={"kind": kind})
        self.assertFalse(serializer.is_valid())
        return serializer.errors

    def test_ids_are_deduplicated_in_order(self):
        data = self.validated({"ids": [3, 1, 3, 2], "action": "set_priority", "value": "high"})
        self.assertEqual(data["ids"], [3, 1, 2])

    def test_a_stage_is_the_kinds_own(self):
        body = {"ids": [1], "action": "set_stage"}
        self.assertEqual(self.validated({**body, "value": "closed_lost"})["value"], "closed_lost")
        self.assertIn("value", self.errors({**body, "value": "mitigated"}))
        self.assertEqual(
            self.validated({**body, "value": "mitigated"}, RISKS)["value"], "mitigated"
        )
        self.assertIn("value", self.errors({**body, "value": ["open"]}, RISKS))

    def test_priority_and_department(self):
        self.validated({"ids": [1], "action": "set_priority", "value": "high"})
        self.assertIn(
            "value", self.errors({"ids": [1], "action": "set_priority", "value": "urgent"})
        )
        for department in ("", "sales"):
            self.validated({"ids": [1], "action": "set_department", "value": department})
        for bad in ("pirates", None):
            self.assertIn(
                "value", self.errors({"ids": [1], "action": "set_department", "value": bad})
            )

    def test_a_date_is_iso_or_an_explicit_null(self):
        body = {"ids": [1], "action": "set_date"}
        self.assertEqual(self.validated({**body, "value": "2026-12-01"})["value"], "2026-12-01")
        self.assertIsNone(self.validated({**body, "value": None})["value"])
        for bad in ("soon", 20261201):
            self.assertIn("value", self.errors({**body, "value": bad}))
        # A forgotten key must not clear every selected date.
        self.assertIn("value", self.errors(body))

    def test_there_is_no_owner_or_delete_action(self):
        for action in ("set_owner", "delete"):
            self.assertIn("action", self.errors({"ids": [1], "action": action, "value": None}))


class BulkEndpointTests(PipelineFixture):
    def post(self, body, user=None, kind="opportunities"):
        api = APIClient()
        api.force_authenticate(user or self.csm)
        return api.post(URL.format(kind), body, format="json")

    def test_set_stage_moves_each_item_and_its_clock(self):
        first, second = self.opportunity("A"), self.opportunity("B")
        long_ago = timezone.now() - timedelta(days=300)
        Opportunity.objects.filter(pk__in=[first.pk, second.pk]).update(stage_changed_at=long_ago)
        response = self.post(
            {"ids": [first.pk, second.pk], "action": "set_stage", "value": "closed_lost"}
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data, {"updated": [first.pk, second.pk], "failed": []})
        for item in Opportunity.objects.filter(pk__in=[first.pk, second.pk]):
            self.assertEqual(item.stage, "closed_lost")
            self.assertGreater(item.stage_changed_at, long_ago)

    def test_a_non_stage_change_leaves_the_clock(self):
        deal = self.opportunity("A")
        long_ago = timezone.now() - timedelta(days=300)
        Opportunity.objects.filter(pk=deal.pk).update(stage_changed_at=long_ago)
        for action, value in (
            ("set_priority", "high"),
            ("set_department", ""),
            ("set_date", "2026-12-01"),
        ):
            response = self.post({"ids": [deal.pk], "action": action, "value": value})
            self.assertEqual(response.data["updated"], [deal.pk])
        deal.refresh_from_db()
        self.assertEqual(deal.stage_changed_at, long_ago)

    def test_setting_the_stage_it_already_has_leaves_the_clock(self):
        risk = self.risk("R")
        long_ago = timezone.now() - timedelta(days=300)
        Risk.objects.filter(pk=risk.pk).update(stage_changed_at=long_ago)
        response = self.post(
            {"ids": [risk.pk], "action": "set_stage", "value": risk.stage}, kind="risks"
        )
        self.assertEqual(response.data["updated"], [risk.pk])
        risk.refresh_from_db()
        self.assertEqual(risk.stage_changed_at, long_ago)

    def test_a_risk_stage_change_moves_its_clock(self):
        risk = self.risk("R")
        long_ago = timezone.now() - timedelta(days=300)
        Risk.objects.filter(pk=risk.pk).update(stage_changed_at=long_ago)
        self.post({"ids": [risk.pk], "action": "set_stage", "value": "mitigated"}, kind="risks")
        risk.refresh_from_db()
        self.assertEqual(risk.stage, "mitigated")
        self.assertGreater(risk.stage_changed_at, long_ago)

    def test_set_date_sets_and_clears_the_kinds_date(self):
        deal = self.opportunity("A")
        self.post({"ids": [deal.pk], "action": "set_date", "value": "2026-12-01"})
        deal.refresh_from_db()
        self.assertEqual(deal.expected_close.isoformat(), "2026-12-01")
        self.post({"ids": [deal.pk], "action": "set_date", "value": None})
        deal.refresh_from_db()
        self.assertIsNone(deal.expected_close)
        risk = self.risk("R")
        self.post({"ids": [risk.pk], "action": "set_date", "value": "2026-11-15"}, kind="risks")
        risk.refresh_from_db()
        self.assertEqual(risk.due_by.isoformat(), "2026-11-15")

    def test_priority_and_department(self):
        deal = self.opportunity("A")
        response = self.post({"ids": [deal.pk], "action": "set_priority", "value": "high"})
        self.assertEqual(response.data["updated"], [deal.pk])
        response = self.post({"ids": [deal.pk], "action": "set_department", "value": ""})
        self.assertEqual(response.data["updated"], [deal.pk])
        deal.refresh_from_db()
        self.assertEqual((deal.priority, deal.department), ("high", ""))

    def test_an_item_the_editor_cannot_read_is_not_found(self):
        viewer, seen, hidden = blind_to_one_account(self.pizza)
        mine = self.opportunity("On seen", account=seen)
        on_hidden = self.opportunity("On hidden", account=hidden)
        sales = self.opportunity("Sales'", department=User.Function.SALES)
        globex = self.opportunity("Globex's", customer=self.globex, department="")
        ids = [mine.pk, on_hidden.pk, sales.pk, globex.pk, MISSING]
        response = self.post({"ids": ids, "action": "set_priority", "value": "low"}, user=viewer)
        self.assertEqual(response.data["updated"], [mine.pk])
        self.assertEqual(
            response.data["failed"],
            [{"id": pk, "reason": "Not found."} for pk in ids[1:]],
        )
        for item in (on_hidden, sales, globex):
            item.refresh_from_db()
            self.assertEqual(item.priority, "medium")

    def test_a_risk_is_not_found_on_the_opportunities_route(self):
        risk = self.risk("R")
        self.assertFalse(Opportunity.objects.exists())
        response = self.post({"ids": [risk.pk], "action": "set_priority", "value": "low"})
        self.assertEqual(response.data["failed"], [{"id": risk.pk, "reason": "Not found."}])

    def test_a_database_error_on_one_id_does_not_stop_the_rest(self):
        first, second = self.opportunity("A"), self.opportunity("B")
        real = bulk_module._apply_one

        def flaky(request, kind, item_id, field, value):
            if item_id == first.pk:
                raise DatabaseError("boom")
            return real(request, kind, item_id, field, value)

        with patch.object(bulk_module, "_apply_one", side_effect=flaky):
            response = self.post(
                {"ids": [first.pk, second.pk], "action": "set_priority", "value": "high"}
            )
        self.assertEqual(
            response.data,
            {
                "updated": [second.pk],
                "failed": [{"id": first.pk, "reason": "Could not be updated."}],
            },
        )

    def test_an_unexpected_error_still_audits_what_was_saved(self):
        first, second = self.opportunity("A"), self.opportunity("B")
        real = bulk_module._apply_one

        def breaks_on_second(request, kind, item_id, field, value):
            if item_id == second.pk:
                raise RuntimeError("boom")
            return real(request, kind, item_id, field, value)

        api = APIClient(raise_request_exception=False)
        api.force_authenticate(self.csm)
        with patch.object(bulk_module, "_apply_one", side_effect=breaks_on_second):
            response = api.post(
                URL.format("opportunities"),
                {"ids": [first.pk, second.pk], "action": "set_priority", "value": "high"},
                format="json",
            )
        self.assertEqual(response.status_code, 500)
        first.refresh_from_db()
        self.assertEqual(first.priority, "high")
        event = AuditEvent.objects.get(action="pipelines.bulk_updated")
        self.assertEqual(event.outcome, AuditEvent.Outcome.FAILURE)
        self.assertEqual((event.metadata["ids"], event.metadata["failed_ids"]), ([first.pk], []))

    def test_each_item_is_locked_on_its_own_table(self):
        risk = self.risk("R")
        with CaptureQueriesContext(connection) as queries:
            self.post({"ids": [risk.pk], "action": "set_priority", "value": "low"}, kind="risks")
        locked = [q["sql"] for q in queries if "FOR UPDATE" in q["sql"]]
        self.assertEqual(len(locked), 1)
        self.assertIn('"customers_risk"', locked[0])

    def test_the_batch_is_audited_once_without_values(self):
        deal = self.opportunity("A")
        self.post({"ids": [deal.pk, MISSING], "action": "set_date", "value": "2026-12-01"})
        [event] = AuditEvent.objects.filter(action="pipelines.bulk_updated")
        self.assertEqual(event.outcome, AuditEvent.Outcome.SUCCESS)
        self.assertEqual(
            event.metadata,
            {
                "kind": "opportunities",
                "action": "set_date",
                "field": "expected_close",
                "ids": [deal.pk],
                "failed_ids": [MISSING],
            },
        )
        self.assertFalse(AuditEvent.objects.filter(action="opportunity.updated").exists())

    def test_a_batch_that_updates_nothing_is_a_failure(self):
        self.post({"ids": [MISSING], "action": "set_priority", "value": "high"})
        event = AuditEvent.objects.get(action="pipelines.bulk_updated")
        self.assertEqual(event.outcome, AuditEvent.Outcome.FAILURE)

    def test_bad_requests(self):
        self.assertEqual(
            self.post({"ids": [], "action": "set_stage", "value": "negotiation"}).status_code, 400
        )
        self.assertEqual(
            self.post({"ids": [1], "action": "set_stage", "value": "open"}).status_code, 400
        )
        self.assertEqual(APIClient().post(URL.format("risks"), {}, format="json").status_code, 401)
