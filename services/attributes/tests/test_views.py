"""Integration tier: AI attributes through the real URLconf and test DB.

The model is never called for real: `get_completion` is patched where
`fill` imports it, and the answers are JSON in the shape the prompt asks
for."""

import json
from datetime import datetime
from datetime import timezone as dt_timezone
from unittest.mock import patch

from django.utils import timezone
from rest_framework.test import APITestCase

from core.models import AuditEvent
from services.accounts.models import Organisation, User
from services.attributes.models import AIAttribute, AIAttributeValue
from services.copilot.anthropic_client import BudgetExceeded
from services.customers.models import Account, Customer, Email, Note

NOW = datetime(2026, 9, 22, 9, 0, tzinfo=dt_timezone.utc)
DEFINITIONS = "/api/v1/attributes/definitions/"
VALUES = "/api/v1/attributes/values/"
COMPLETION = "services.attributes.fill.get_completion"


def answer(value, reasoning="Two notes say so.", evidence=(0,)):
    return json.dumps({"value": value, "reasoning": reasoning, "evidence": list(evidence)})


class Fixture(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        mk = lambda email, name, **kw: User.objects.create_user(  # noqa: E731
            email=email, password="x", name=name, organisation=self.org, **kw
        )
        self.alice = mk("alice@acme.io", "Alice", role=User.Role.ADMIN)
        self.dana = mk("dana@acme.io", "Dana", role=User.Role.CSM)
        self.eve = mk("eve@acme.io", "Eve", role=User.Role.CSM)
        self.pizza = Customer.objects.create(
            organisation=self.org, name="Pizza Hut", domain="pizzahut.com", owner=self.dana
        )
        self.burger = Customer.objects.create(
            organisation=self.org, name="Burger King", domain="bk.com", owner=self.dana
        )
        self.apac = Account.objects.create(name="APAC", domain="apac.pizzahut.com", owner=self.dana)
        self.apac.customers.add(self.pizza)
        Note.objects.create(
            customer=self.pizza,
            author=self.dana,
            title="Product usage",
            logged_at=NOW,
            body="They run the Enterprise tier across three regions.",
        )
        self.tier = AIAttribute.objects.create(
            organisation=self.org,
            name="Product tier",
            api_name="product_tier",
            prompt="Which tier of our product does this company use?",
            value_type=AIAttribute.ValueType.PICKLIST,
            picklist_options=["SMB", "Enterprise"],
            applies_to_customer=True,
            applies_to_account=True,
            created_by=self.alice,
        )


class Definitions(Fixture):
    def test_admin_defines_an_attribute_and_it_is_audited(self):
        self.client.force_authenticate(self.alice)
        response = self.client.post(
            DEFINITIONS,
            {
                "name": "Seats in use",
                "prompt": "How many seats does this company actively use?",
                "value_type": "number",
                "applies_to_customer": True,
                "applies_to_account": False,
                "refresh": "nightly",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["api_name"], "seats_in_use")
        self.assertEqual(response.data["refresh"], "nightly")
        self.assertTrue(AuditEvent.objects.filter(action="attribute.define").exists())

    def test_picklist_needs_options_and_a_parent_type_is_required(self):
        self.client.force_authenticate(self.alice)
        response = self.client.post(
            DEFINITIONS,
            {"name": "Tier", "prompt": "Which tier?", "value_type": "picklist"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("picklist_options", response.data)
        response = self.client.post(
            DEFINITIONS,
            {
                "name": "Tier",
                "prompt": "Which tier?",
                "value_type": "text",
                "applies_to_customer": False,
                "applies_to_account": False,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_a_csm_reads_but_cannot_write(self):
        self.client.force_authenticate(self.dana)
        self.assertEqual(self.client.get(DEFINITIONS).status_code, 200)
        self.assertEqual(len(self.client.get(DEFINITIONS).data), 1)
        response = self.client.post(
            DEFINITIONS, {"name": "X", "prompt": "?", "value_type": "text"}, format="json"
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.delete(f"{DEFINITIONS}{self.tier.id}/").status_code, 403)

    def test_another_tenant_sees_nothing(self):
        other = Organisation.objects.create(name="Other")
        outsider = User.objects.create_user(
            email="o@other.io", password="x", name="O", organisation=other, role=User.Role.ADMIN
        )
        self.client.force_authenticate(outsider)
        self.assertEqual(self.client.get(DEFINITIONS).data, [])
        self.assertEqual(self.client.get(f"{DEFINITIONS}{self.tier.id}/").status_code, 404)


class FillOne(Fixture):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.dana)
        self.url = f"{DEFINITIONS}{self.tier.id}/fill/"

    @patch(COMPLETION, return_value=answer("enterprise"))
    def test_fills_a_customer_with_reasoning_and_cited_sources(self, completion):
        response = self.client.post(self.url, {"customer": self.pizza.id}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        row = response.data
        self.assertEqual(row["value"], "Enterprise")
        self.assertEqual(row["status"], "filled")
        self.assertEqual(row["origin"], "ai")
        self.assertEqual(row["reasoning"], "Two notes say so.")
        self.assertEqual([s["label"] for s in row["sources"]], ["Product usage"])
        self.assertEqual(row["customer"], self.pizza.id)
        self.assertIsNone(row["account"])
        kwargs = completion.call_args.kwargs
        self.assertEqual(kwargs["purpose"], "attribute")
        self.assertIn("Which tier of our product", kwargs["system"])
        self.assertIn("Enterprise tier across three regions", kwargs["system"])
        self.assertIn("SMB, Enterprise", kwargs["system"])
        self.assertTrue(AuditEvent.objects.filter(action="attribute.fill").exists())

    @patch(COMPLETION, return_value=answer("SMB"))
    def test_fills_an_account(self, completion):
        response = self.client.post(self.url, {"account": self.apac.id}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["account"], self.apac.id)
        self.assertIsNone(response.data["customer"])

    @patch(COMPLETION, return_value=answer(None, "Nothing on record mentions a tier.", ()))
    def test_no_evidence_is_recorded_as_insufficient(self, completion):
        response = self.client.post(self.url, {"customer": self.pizza.id}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["status"], "insufficient")
        self.assertIsNone(response.data["value"])
        self.assertEqual(response.data["reasoning"], "Nothing on record mentions a tier.")

    @patch(COMPLETION, return_value=answer("Mid-market"))
    def test_an_answer_outside_the_picklist_is_a_failed_fill(self, completion):
        response = self.client.post(self.url, {"customer": self.pizza.id}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["status"], "failed")
        self.assertIn("Mid-market", response.data["reasoning"])

    @patch(COMPLETION, side_effect=BudgetExceeded("spent"))
    def test_budget_exhausted_is_429(self, completion):
        response = self.client.post(self.url, {"customer": self.pizza.id}, format="json")
        self.assertEqual(response.status_code, 429)
        self.assertFalse(AIAttributeValue.objects.exists())

    def test_a_company_the_viewer_cannot_see_is_404(self):
        self.client.force_authenticate(self.eve)
        response = self.client.post(self.url, {"customer": self.pizza.id}, format="json")
        self.assertEqual(response.status_code, 404)

    def test_an_attribute_that_does_not_apply_to_accounts_refuses_one(self):
        self.tier.applies_to_account = False
        self.tier.save()
        response = self.client.post(self.url, {"account": self.apac.id}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_disabled_copilot_is_403(self):
        self.org.ai_agent_enabled = False
        self.org.save()
        response = self.client.post(self.url, {"customer": self.pizza.id}, format="json")
        self.assertEqual(response.status_code, 403)


class FillAll(Fixture):
    @patch(COMPLETION, return_value=answer("SMB"))
    def test_fills_every_applicable_company_up_to_the_cap(self, completion):
        self.client.force_authenticate(self.dana)
        with patch("services.attributes.views.FILL_CAP", 2):
            response = self.client.post(f"{DEFINITIONS}{self.tier.id}/fill/", {}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        # Two customers and one account apply; the cap stops after two.
        self.assertEqual(response.data["filled"], 2)
        self.assertEqual(response.data["remaining"], 1)
        self.assertEqual(completion.call_count, 2)

    @patch(COMPLETION, return_value=answer("SMB"))
    def test_only_companies_the_viewer_sees_are_filled(self, completion):
        self.burger.owner = self.eve
        self.burger.save()
        self.client.force_authenticate(self.eve)
        response = self.client.post(f"{DEFINITIONS}{self.tier.id}/fill/", {}, format="json")
        self.assertEqual(response.data["filled"], 1)
        self.assertEqual(AIAttributeValue.objects.get().customer, self.burger)


class ValuesAndHistory(Fixture):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.dana)
        self.seats = AIAttribute.objects.create(
            organisation=self.org,
            name="Seats in use",
            api_name="seats_in_use",
            prompt="How many seats?",
            value_type=AIAttribute.ValueType.NUMBER,
            applies_to_customer=True,
            applies_to_account=False,
        )

    def fill(self, value):
        with patch(COMPLETION, return_value=answer(value)):
            return self.client.post(
                f"{DEFINITIONS}{self.tier.id}/fill/", {"customer": self.pizza.id}, format="json"
            )

    def test_latest_value_per_applicable_attribute_including_unfilled(self):
        self.fill("SMB")
        self.fill("Enterprise")
        response = self.client.get(f"{VALUES}?customer={self.pizza.id}")
        self.assertEqual(response.status_code, 200, response.data)
        by_name = {row["attribute"]["name"]: row for row in response.data}
        self.assertEqual(set(by_name), {"Product tier", "Seats in use"})
        self.assertEqual(by_name["Product tier"]["latest"]["value"], "Enterprise")
        self.assertIsNone(by_name["Seats in use"]["latest"])
        # The account only gets the attribute that applies to accounts.
        response = self.client.get(f"{VALUES}?account={self.apac.id}")
        self.assertEqual([row["attribute"]["name"] for row in response.data], ["Product tier"])

    def test_history_is_every_row_newest_first(self):
        self.fill("SMB")
        self.fill("Enterprise")
        response = self.client.get(
            f"{VALUES}history/?attribute={self.tier.id}&customer={self.pizza.id}"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([row["value"] for row in response.data], ["Enterprise", "SMB"])

    def test_a_person_overrides_and_the_override_is_the_latest(self):
        self.fill("SMB")
        response = self.client.post(
            VALUES,
            {"attribute": self.tier.id, "customer": self.pizza.id, "value": "enterprise"},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["origin"], "human")
        self.assertEqual(response.data["value"], "Enterprise")
        self.assertEqual(response.data["set_by"]["name"], "Dana")
        latest = self.client.get(f"{VALUES}?customer={self.pizza.id}").data
        tier = next(r for r in latest if r["attribute"]["id"] == self.tier.id)
        self.assertEqual(tier["latest"]["origin"], "human")
        self.assertTrue(AuditEvent.objects.filter(action="attribute.override").exists())

    def test_an_override_outside_the_picklist_is_400(self):
        response = self.client.post(
            VALUES,
            {"attribute": self.tier.id, "customer": self.pizza.id, "value": "Mid-market"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_values_of_an_invisible_company_are_404(self):
        self.client.force_authenticate(self.eve)
        self.assertEqual(self.client.get(f"{VALUES}?customer={self.pizza.id}").status_code, 404)
        self.assertEqual(self.client.get(VALUES).status_code, 400)


class Nightly(Fixture):
    """`refresh_nightly` runs from run_health_maintenance: only nightly
    attributes, only companies with no value yet or with classified
    activity newer than their last value."""

    def setUp(self):
        super().setUp()
        self.tier.refresh = AIAttribute.Refresh.NIGHTLY
        self.tier.applies_to_account = False
        self.tier.save()
        AIAttribute.objects.create(
            organisation=self.org,
            name="Manual only",
            api_name="manual_only",
            prompt="?",
            value_type=AIAttribute.ValueType.TEXT,
            refresh=AIAttribute.Refresh.MANUAL,
        )

    @patch(COMPLETION, return_value=answer("SMB"))
    def test_fills_missing_and_stale_companies_only(self, completion):
        from services.attributes.fill import refresh_nightly

        self.assertEqual(refresh_nightly(), 2)  # both customers had no value
        self.assertEqual(completion.call_count, 2)
        self.assertEqual(refresh_nightly(), 0)  # nothing new since
        Email.objects.create(
            customer=self.pizza,
            subject="Upgrade",
            body="We moved to Enterprise.",
            sent_at=timezone.now(),
            ai_classified_at=timezone.now(),
        )
        self.assertEqual(refresh_nightly(), 1)
        self.assertEqual(
            AIAttributeValue.objects.filter(attribute=self.tier, customer=self.pizza).count(), 2
        )

    @patch(COMPLETION, side_effect=BudgetExceeded("spent"))
    def test_a_budget_stop_ends_the_run_quietly(self, completion):
        from services.attributes.fill import refresh_nightly

        self.assertEqual(refresh_nightly(), 0)
