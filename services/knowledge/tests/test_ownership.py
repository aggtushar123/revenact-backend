"""The account owner: one accountable person, gated changes, handovers on the record."""

from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.customers.models import Customer
from services.knowledge.models import Contribution, FunctionOwner
from services.notifications.models import Notification


class OwnershipTests(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        mk = lambda email, name, function, role=User.Role.CSM, boss=None: User.objects.create_user(  # noqa: E731
            email=email,
            password="x",
            name=name,
            organisation=self.org,
            role=role,
            function=function,
            reports_to=boss,
        )
        self.alice = mk("alice@acme.io", "Alice Admin", "leadership", User.Role.ADMIN)
        self.carl = mk("carl@acme.io", "Carl CSM", "cs", boss=self.alice)
        self.dana = mk("dana@acme.io", "Dana CSM", "cs", boss=self.carl)
        self.priya = mk("priya@acme.io", "Priya Nair", "engineering", boss=self.alice)
        self.pizza = Customer.objects.create(
            organisation=self.org, name="Pizza Hut", owner=self.dana
        )

    def test_only_the_owner_their_chain_or_a_settings_manager_reassigns(self):
        url = f"/api/v1/customers/{self.pizza.id}/"
        # Priya answers for engineering on the account (so she can open it)
        # but is on another branch with no capability: she may not take it.
        FunctionOwner.objects.create(customer=self.pizza, function="engineering", user=self.priya)
        self.client.force_authenticate(self.priya)
        self.assertEqual(
            self.client.patch(url, {"owner_id": self.priya.id}, format="json").status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        # Carl manages Dana: allowed, with a note that becomes knowledge.
        self.client.force_authenticate(self.carl)
        response = self.client.patch(
            url,
            {
                "owner_id": self.priya.id,
                "handover_note": "Technical-led from here; Priya takes it.",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.pizza.refresh_from_db()
        self.assertEqual(self.pizza.owner, self.priya)
        note = Contribution.objects.get(customer=self.pizza)
        self.assertEqual(note.author, self.carl)
        self.assertEqual(
            note.body,
            "Account owner changed from Dana CSM to Priya Nair. "
            "Technical-led from here; Priya takes it.",
        )
        self.assertTrue(
            Notification.objects.filter(recipient=self.priya, kind="customer_assigned").exists()
        )
        # The new owner (any function) may hand it on; nothing is written when nothing changes.
        self.client.force_authenticate(self.priya)
        self.assertEqual(
            self.client.patch(url, {"owner_id": self.priya.id}, format="json").status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(Contribution.objects.filter(customer=self.pizza).count(), 1)

    def test_the_company_view_row_is_the_same_owner_with_the_same_rules(self):
        url = f"/api/v1/customers/{self.pizza.id}/responsible/"
        self.client.force_authenticate(self.priya)
        self.assertEqual(
            self.client.patch(
                url, {"function": "cs", "user_id": self.priya.id}, format="json"
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.client.force_authenticate(self.alice)
        data = self.client.patch(
            url,
            {"function": "cs", "user_id": self.carl.id, "note": "Dana is on leave."},
            format="json",
        ).data
        self.assertEqual(
            data["account_owner"], {"id": self.carl.id, "name": "Carl CSM", "function": "cs"}
        )
        self.assertIn("Dana is on leave.", Contribution.objects.get(customer=self.pizza).body)

    def test_an_unowned_customer_may_be_claimed_by_anyone(self):
        loose = Customer.objects.create(organisation=self.org, name="Loose")
        self.client.force_authenticate(self.priya)
        self.assertEqual(
            self.client.patch(
                f"/api/v1/customers/{loose.id}/", {"owner_id": self.priya.id}, format="json"
            ).status_code,
            status.HTTP_200_OK,
        )

    def test_your_book_means_owned_or_responsible(self):
        from services.copilot.context import build_grounding

        FunctionOwner.objects.create(customer=self.pizza, function="engineering", user=self.priya)
        summary = build_grounding(self.org, self.priya).summary
        self.assertIn("Your customers: 1 total", summary)
        self.assertNotIn("You own no customers", summary)

    def test_the_ops_agent_sees_the_account_team_and_is_told_to_use_it(self):
        from datetime import timedelta
        from decimal import Decimal

        from django.utils import timezone

        from services.metrics import proposals

        Customer.objects.filter(pk=self.pizza.pk).update(
            arr_billed_at_account=Decimal(100_000),
            health_score=Decimal("2.0"),
            renewal_date=timezone.localdate() + timedelta(days=30),
        )
        FunctionOwner.objects.create(customer=self.pizza, function="engineering", user=self.priya)
        prompt = proposals.build_prompt(proposals.build_evidence(self.org))
        self.assertIn("account owner Dana CSM (team: Engineering Priya Nair)", prompt)
        self.assertIn("responsible for the function the task needs", proposals.SYSTEM_PROMPT)
