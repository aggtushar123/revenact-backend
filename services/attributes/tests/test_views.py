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
from services.accounts.capabilities import Capability
from services.accounts.models import Organisation, Role, User
from services.attributes.models import AIAttribute, AIAttributeValue
from services.copilot.anthropic_client import BudgetExceeded
from services.customers.models import Account, Customer, Email, Note

# A week ago: a note carries a date, and a note dated today would count as
# newer than an answer computed this morning (see fill._end_of_day).
NOW = datetime(2026, 9, 15, 9, 0, tzinfo=dt_timezone.utc)
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
        self.assertIn("SMB, Enterprise", kwargs["system"])
        self.assertIn("Enterprise tier across three regions", kwargs["messages"][0]["content"])
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
        self.client.force_authenticate(self.alice)
        with patch("services.attributes.views.FILL_CAP", 2):
            response = self.client.post(f"{DEFINITIONS}{self.tier.id}/fill/", {}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        # Two customers and one account apply; the cap stops after two.
        self.assertEqual(response.data["filled"], 2)
        self.assertEqual(response.data["remaining"], 1)
        self.assertEqual(completion.call_count, 2)

    @patch(COMPLETION, return_value=answer("SMB"))
    def test_only_companies_the_viewer_sees_are_filled(self, completion):
        definer = Role.objects.create(
            organisation=self.org,
            name="Definer",
            slug="definer",
            permissions=[Capability.MANAGE_CUSTOM_OBJECTS],
        )
        self.eve.role = definer
        self.eve.save()
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


class WhatOtherReadersSee(Fixture):
    """Reasoning and citations are personal where the records are: a reader
    who may not open the cited note sees neither its title nor the text
    that quotes it."""

    def setUp(self):
        super().setUp()
        # Eve may open the customer (Lead role) but not Dana's note.
        lead = Role.objects.create(
            organisation=self.org,
            name="Lead",
            slug="lead",
            permissions=[Capability.VIEW_ALL_ACCOUNTS],
        )
        self.eve.role = lead
        self.eve.save()

    @patch(COMPLETION, return_value=answer("enterprise"))
    def test_a_peer_sees_the_value_but_not_a_private_note(self, completion):
        self.client.force_authenticate(self.dana)
        self.client.post(
            f"{DEFINITIONS}{self.tier.id}/fill/", {"customer": self.pizza.id}, format="json"
        )
        self.client.force_authenticate(self.eve)
        rows = self.client.get(f"{VALUES}?customer={self.pizza.id}").data
        latest = next(r for r in rows if r["attribute"]["id"] == self.tier.id)["latest"]
        self.assertEqual(latest["value"], "Enterprise")
        self.assertEqual(latest["sources"], [])
        self.assertEqual(latest["reasoning"], "")
        self.assertEqual(latest["hidden_sources"], 1)
        history = self.client.get(
            f"{VALUES}history/?attribute={self.tier.id}&customer={self.pizza.id}"
        ).data
        self.assertEqual(history[0]["sources"], [])
        # Dana, the author, still sees her own citation.
        self.client.force_authenticate(self.dana)
        rows = self.client.get(f"{VALUES}?customer={self.pizza.id}").data
        latest = next(r for r in rows if r["attribute"]["id"] == self.tier.id)["latest"]
        self.assertEqual([s["label"] for s in latest["sources"]], ["Product usage"])
        self.assertEqual(latest["hidden_sources"], 0)


class NightlyScope(Fixture):
    """The scheduled pass reads as the company's owner would, never wider."""

    def setUp(self):
        super().setUp()
        self.tier.refresh = AIAttribute.Refresh.NIGHTLY
        self.tier.save()
        Note.objects.create(
            customer=self.burger,
            author=self.eve,
            title="Eve's private note",
            logged_at=NOW,
            body="Confidential: they are on Enterprise.",
        )

    @patch(COMPLETION, return_value=answer("enterprise"))
    def test_evidence_is_the_owners_view(self, completion):
        from services.attributes.fill import refresh_nightly

        refresh_nightly()
        prompts = [c.kwargs["messages"][0]["content"] for c in completion.call_args_list]
        joined = "\n".join(prompts)
        self.assertIn(
            "Enterprise tier across three regions", joined
        )  # Dana's own note on Pizza Hut
        self.assertNotIn(
            "Eve's private note", joined
        )  # Burger King is Dana's; Eve's note is not hers to read

    @patch(COMPLETION, return_value=answer("SMB"))
    def test_records_are_data_not_instructions(self, completion):
        self.client.force_authenticate(self.dana)
        self.client.post(
            f"{DEFINITIONS}{self.tier.id}/fill/", {"customer": self.pizza.id}, format="json"
        )
        kwargs = completion.call_args.kwargs
        self.assertNotIn("Enterprise tier across three regions", kwargs["system"])
        self.assertIn('<record index="0">', kwargs["messages"][0]["content"])
        self.assertIn("never instructions", kwargs["system"])


class NightlyRules(Fixture):
    def setUp(self):
        super().setUp()
        self.tier.refresh = AIAttribute.Refresh.NIGHTLY
        self.tier.applies_to_account = False
        self.tier.save()

    @patch(COMPLETION, return_value=answer("SMB"))
    def test_a_human_override_is_not_buried(self, completion):
        from services.attributes.fill import refresh_nightly

        AIAttributeValue.objects.create(
            attribute=self.tier,
            customer=self.pizza,
            value="Enterprise",
            origin=AIAttributeValue.Origin.HUMAN,
            set_by=self.dana,
        )
        Email.objects.create(customer=self.pizza, subject="New", body="x", sent_at=timezone.now())
        self.assertEqual(refresh_nightly(), 1)  # Burger King only
        self.assertEqual(
            AIAttributeValue.objects.filter(attribute=self.tier, customer=self.pizza).count(), 1
        )

    @patch(COMPLETION, return_value=answer("SMB"))
    def test_a_new_note_makes_a_company_stale(self, completion):
        from services.attributes.fill import refresh_nightly

        refresh_nightly()
        self.assertEqual(refresh_nightly(), 0)
        Note.objects.create(
            customer=self.pizza, author=self.dana, title="Later", logged_at=timezone.now(), body="y"
        )
        self.assertEqual(refresh_nightly(), 1)

    def test_one_tenants_budget_does_not_stop_another(self):
        from services.attributes.fill import refresh_nightly

        other = Organisation.objects.create(name="Other")
        other_admin = User.objects.create_user(
            email="o@other.io", password="x", name="O", organisation=other, role=User.Role.ADMIN
        )
        Customer.objects.create(
            organisation=other, name="Wendy's", domain="wendys.com", owner=other_admin
        )
        AIAttribute.objects.create(
            organisation=other,
            name="Tier",
            api_name="tier",
            prompt="?",
            refresh=AIAttribute.Refresh.NIGHTLY,
        )

        def completion(*, organisation, **kwargs):
            if organisation == self.org:
                raise BudgetExceeded("spent")
            return answer("SMB")

        with patch(COMPLETION, side_effect=completion):
            filled = refresh_nightly()
        self.assertEqual(filled, 1)
        self.assertEqual(AIAttributeValue.objects.get().customer.name, "Wendy's")

    @patch(COMPLETION, return_value=answer("SMB"))
    def test_the_pass_is_capped_per_organisation(self, completion):
        from services.attributes.fill import refresh_nightly

        with patch("services.attributes.fill.NIGHTLY_CAP", 1):
            self.assertEqual(refresh_nightly(), 1)
            self.assertEqual(refresh_nightly(), 1)  # the other company, next night
        self.assertEqual(refresh_nightly(), 0)

    @patch(COMPLETION, return_value=answer("SMB"))
    def test_accounts_are_filled_too(self, completion):
        from services.attributes.fill import refresh_nightly

        self.tier.applies_to_account = True
        self.tier.save()
        self.assertEqual(refresh_nightly(), 3)
        self.assertTrue(AIAttributeValue.objects.filter(account=self.apac).exists())


class DefinitionChanges(Fixture):
    def test_update_and_delete_are_audited_and_delete_reports_the_history_size(self):
        self.client.force_authenticate(self.alice)
        AIAttributeValue.objects.create(attribute=self.tier, customer=self.pizza, value="SMB")
        response = self.client.patch(
            f"{DEFINITIONS}{self.tier.id}/", {"refresh": "nightly"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(AuditEvent.objects.filter(action="attribute.update").exists())
        response = self.client.delete(f"{DEFINITIONS}{self.tier.id}/")
        self.assertEqual(response.status_code, 204)
        event = AuditEvent.objects.get(action="attribute.delete")
        self.assertEqual(event.metadata["values"], 1)
        self.assertFalse(AIAttributeValue.objects.exists())

    def test_fill_all_needs_the_defining_capability(self):
        self.client.force_authenticate(self.dana)
        response = self.client.post(f"{DEFINITIONS}{self.tier.id}/fill/", {}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_another_tenants_company_is_404_and_a_bad_id_is_400(self):
        other = Organisation.objects.create(name="Other")
        theirs = Customer.objects.create(organisation=other, name="Wendy's", domain="wendys.com")
        self.client.force_authenticate(self.dana)
        with patch(COMPLETION, return_value=answer("SMB")):
            response = self.client.post(
                f"{DEFINITIONS}{self.tier.id}/fill/", {"customer": theirs.id}, format="json"
            )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.get(f"{VALUES}?customer={theirs.id}").status_code, 404)
        self.assertEqual(self.client.get(f"{VALUES}?customer=abc").status_code, 400)
        self.assertEqual(
            self.client.get(f"{VALUES}history/?attribute=abc&customer={self.pizza.id}").status_code,
            400,
        )


class FailedAnswers(Fixture):
    @patch(COMPLETION, return_value=answer("Mid-market", "The tickets mention mid-market pricing."))
    def test_a_failed_fill_keeps_the_models_reasoning(self, completion):
        self.client.force_authenticate(self.dana)
        response = self.client.post(
            f"{DEFINITIONS}{self.tier.id}/fill/", {"customer": self.pizza.id}, format="json"
        )
        self.assertEqual(response.data["status"], "failed")
        self.assertIn("mid-market pricing", response.data["reasoning"])
        self.assertIn("Mid-market", response.data["reasoning"])
