"""Opportunity and risk writes are audited: create, edit (a drag included)
and delete, from every endpoint that writes them, naming ids and changed
field names only — never a value, never the title."""

from rest_framework.test import APITestCase

from core.models import AuditEvent
from services.accounts.models import Organisation, User
from services.customers.models import Account, Customer, Opportunity, Risk


class PipelineAuditTests(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.csm = User.objects.create_user(
            email="carl@acme.io",
            password="supersecret1",
            name="Carl",
            organisation=self.org,
            role=User.Role.CSM,
        )
        self.pizza = Customer.objects.create(
            organisation=self.org, name="Pizza Hut", owner=self.csm
        )
        self.emea = Account.objects.create(name="Pizza EMEA", owner=self.csm)
        self.emea.customers.add(self.pizza)
        self.client.force_authenticate(self.csm)

    def events(self, action):
        return list(AuditEvent.objects.filter(action=action).order_by("id"))

    def test_every_create_path_records_one_event(self):
        paths = [
            ("/api/v1/opportunities/", {"customer_id": self.pizza.pk}),
            ("/api/v1/opportunities/", {"account_id": self.emea.pk}),
            (f"/api/v1/customers/{self.pizza.pk}/opportunities/", {}),
            (f"/api/v1/customers/{self.pizza.pk}/accounts/{self.emea.pk}/opportunities/", {}),
            (f"/api/v1/accounts/{self.emea.pk}/opportunities/", {}),
        ]
        ids = []
        for url, parent in paths:
            response = self.client.post(
                url, {"title": "Secret upsell", "mrr": "10", **parent}, format="json"
            )
            self.assertEqual(response.status_code, 201, (url, response.data))
            ids.append(str(response.data["id"]))
        events = self.events("opportunity.created")
        self.assertEqual([event.target_id for event in events], ids)
        self.assertEqual({event.target_type for event in events}, {"customers.opportunity"})
        self.assertEqual(events[0].actor, self.csm)
        self.assertEqual(events[0].metadata, {"customer_id": self.pizza.pk, "account_id": None})
        self.assertEqual(events[1].metadata, {"customer_id": None, "account_id": self.emea.pk})
        for event in events:
            self.assertEqual(event.target_repr, f"opportunity {event.target_id}")
            self.assertNotIn("Secret", str(event.metadata))

    def test_a_risk_create_is_recorded_as_a_risk(self):
        response = self.client.post(
            f"/api/v1/customers/{self.pizza.pk}/risks/",
            {"title": "Budget", "mrr": "5"},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        [event] = self.events("risk.created")
        pk = response.data["id"]
        self.assertEqual(
            (event.target_type, event.target_id, event.target_repr),
            ("customers.risk", str(pk), f"risk {pk}"),
        )

    def test_an_edit_names_the_changed_fields_and_not_their_values(self):
        item = Opportunity.objects.create(customer=self.pizza, title="Upsell", mrr=100)
        response = self.client.patch(
            f"/api/v1/opportunities/{item.pk}/",
            {"title": "Secret new title", "mrr": "100", "expected_close": "2026-12-01"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        [event] = self.events("opportunity.updated")
        self.assertEqual(event.metadata, {"fields": ["expected_close", "title"]})
        self.assertNotIn("Secret", event.target_repr)

    def test_a_drag_is_an_edit_of_the_stage(self):
        item = Risk.objects.create(customer=self.pizza, title="Budget")
        response = self.client.patch(
            f"/api/v1/risks/{item.pk}/", {"stage": "mitigated"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        [event] = self.events("risk.updated")
        self.assertEqual(event.metadata, {"fields": ["stage"]})

    def test_a_patch_that_changes_nothing_or_fails_records_nothing(self):
        item = Opportunity.objects.create(customer=self.pizza, title="Upsell", mrr=100)
        url = f"/api/v1/opportunities/{item.pk}/"
        same = self.client.patch(url, {"title": "Upsell", "mrr": "100.00"}, format="json")
        self.assertEqual(same.status_code, 200)
        self.assertEqual(self.client.patch(url, {"stage": "won"}, format="json").status_code, 400)
        self.assertEqual(self.events("opportunity.updated"), [])

    def test_a_delete_is_recorded_with_its_parent(self):
        item = Opportunity.objects.create(account=self.emea, title="Upsell")
        pk = item.pk
        self.assertEqual(self.client.delete(f"/api/v1/opportunities/{pk}/").status_code, 204)
        self.assertFalse(Opportunity.objects.filter(pk=pk).exists())
        [event] = self.events("opportunity.deleted")
        self.assertEqual(
            (event.target_id, event.metadata),
            (str(pk), {"customer_id": None, "account_id": self.emea.pk}),
        )

    def test_a_delete_the_caller_may_not_make_records_nothing(self):
        dana = User.objects.create_user(
            email="dana@acme.io",
            password="supersecret1",
            name="Dana",
            organisation=self.org,
            role=User.Role.CSM,
        )
        theirs = Customer.objects.create(organisation=self.org, name="Not Carl's", owner=dana)
        item = Opportunity.objects.create(customer=theirs, title="Theirs")
        self.assertEqual(self.client.delete(f"/api/v1/opportunities/{item.pk}/").status_code, 404)
        self.assertEqual(self.events("opportunity.deleted"), [])
