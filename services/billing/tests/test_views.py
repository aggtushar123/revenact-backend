"""A tenant sees only its own account; staff see every account and can move
credits, seats and plans, each with a reason and on the record."""

from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken

from core.models import AuditEvent
from services.accounts.models import Organisation, User
from services.billing import accounts, ledger
from services.billing.models import Plan


def staff_token(user):
    token = AccessToken.for_user(user)
    token["mfa"] = True
    return str(token)


class TenantBillingTests(APITestCase):
    def setUp(self):
        self.acme = Organisation.objects.create(name="Acme")
        self.rival = Organisation.objects.create(name="Rival")
        self.admin = User.objects.create_user(
            email="admin@acme.io",
            password="x",
            name="A",
            organisation=self.acme,
            role=User.Role.ADMIN,
        )
        self.csm = User.objects.create_user(
            email="csm@acme.io", password="x", name="C", organisation=self.acme
        )
        ledger.grant(accounts.ensure(self.rival), 5000, reason="rival's money", reference="r")

    def test_any_member_sees_the_summary_of_their_own_organisation_only(self):
        self.client.force_authenticate(self.csm)
        response = self.client.get("/api/v1/billing/account/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["plan"]["code"], "trial")
        self.assertEqual(response.data["seats"], {"used": 2, "limit": 3})
        self.assertEqual(response.data["credits"]["balance"], 200, "not Rival's 5200")

    def test_the_ledger_needs_manage_org_settings(self):
        self.client.force_authenticate(self.csm)
        self.assertEqual(
            self.client.get("/api/v1/billing/ledger/").status_code, status.HTTP_403_FORBIDDEN
        )
        self.client.force_authenticate(self.admin)
        rows = self.client.get("/api/v1/billing/ledger/").data
        self.assertEqual([r["kind"] for r in rows], ["grant"])
        self.assertEqual(rows[0]["balance_after"], 200)

    def test_plans_lists_only_public_ones(self):
        Plan.objects.create(
            code="team", name="Team", seats_included=10, monthly_credits=2000, price_cents=4900
        )
        Plan.objects.create(
            code="secret", name="Secret", seats_included=99, monthly_credits=9, is_public=False
        )
        self.client.force_authenticate(self.csm)
        codes = [p["code"] for p in self.client.get("/api/v1/billing/plans/").data]
        self.assertEqual(codes, ["team"])

    def test_adding_a_member_past_the_allowance_is_refused_with_402(self):
        self.client.force_authenticate(self.admin)
        User.objects.create_user(
            email="third@acme.io", password="x", name="T", organisation=self.acme
        )
        response = self.client.post(
            "/api/v1/auth/users/",
            {
                "email": "fourth@acme.io",
                "name": "F",
                "password": "a-long-random-password-1",
                "role_id": self.csm.role_id,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)
        self.assertEqual(response.data["error"]["code"], "INSUFFICIENT_SEATS")
        self.assertFalse(User.objects.filter(email="fourth@acme.io").exists())

    def test_deactivating_frees_a_seat_and_reactivating_takes_one(self):
        self.client.force_authenticate(self.admin)
        third = User.objects.create_user(
            email="third@acme.io", password="x", name="T", organisation=self.acme
        )
        account = accounts.ensure(self.acme)
        from services.billing import seats

        self.assertEqual(seats.used(account), 3)
        self.client.patch(f"/api/v1/auth/users/{third.id}/", {"is_active": False}, format="json")
        self.assertEqual(seats.used(account), 2)

        # Someone else takes the freed seat; the deactivated person cannot come back.
        User.objects.create_user(
            email="fourth@acme.io", password="x", name="F", organisation=self.acme
        )
        response = self.client.patch(
            f"/api/v1/auth/users/{third.id}/", {"is_active": True}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)
        self.assertFalse(User.objects.get(pk=third.pk).is_active)


class PlatformBillingTests(APITestCase):
    def setUp(self):
        self.acme = Organisation.objects.create(name="Acme")
        self.owner = User.objects.create_user(
            email="owner@acme.io",
            password="x",
            name="O",
            organisation=self.acme,
            role=User.Role.ADMIN,
        )
        self.staff = User.objects.create_superuser(email="s@revenact.io", password="x", name="S")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {staff_token(self.staff)}")
        self.team = Plan.objects.create(
            code="team", name="Team", seats_included=10, monthly_credits=2000, price_cents=4900
        )

    def test_the_billing_panel(self):
        response = self.client.get(f"/api/v1/platform/organisations/{self.acme.id}/billing/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["plan"]["code"], "trial")
        self.assertEqual(response.data["seats"], {"used": 1, "limit": 3})
        self.assertEqual([r["kind"] for r in response.data["ledger"]], ["grant"])
        self.assertIn("team", [p["code"] for p in response.data["plans"]])

    def test_credits_seats_and_plan_each_need_a_reason_and_are_audited(self):
        base = f"/api/v1/platform/organisations/{self.acme.id}/billing/"
        self.assertEqual(
            self.client.post(base + "credits/", {"amount": 100}, format="json").data["error"][
                "code"
            ],
            "REASON_REQUIRED",
        )

        credits = self.client.post(
            base + "credits/", {"amount": 100, "reason": "Onboarding gift"}, format="json"
        )
        self.assertEqual(credits.status_code, status.HTTP_200_OK)
        self.assertEqual(credits.data["credits"]["balance"], 300)

        seats = self.client.post(
            base + "seats/", {"seats_limit": 8, "reason": "Pilot"}, format="json"
        )
        self.assertEqual(seats.data["seats"]["limit"], 8)

        plan = self.client.post(
            base + "plan/", {"plan_code": "team", "reason": "Signed"}, format="json"
        )
        self.assertEqual(plan.data["plan"]["code"], "team")
        self.assertEqual(plan.data["status"], "active")
        self.assertEqual(plan.data["seats"]["limit"], 10)
        self.assertEqual(plan.data["credits"]["balance"], 2300)

        actions = set(
            AuditEvent.objects.filter(organisation=self.acme).values_list("action", flat=True)
        )
        self.assertTrue(
            {"billing.credits.adjusted", "billing.seats.changed", "billing.plan.changed"} <= actions
        )

    def test_a_tenant_admin_cannot_reach_the_platform_billing_routes(self):
        token = AccessToken.for_user(self.owner)
        token["mfa"] = True
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(
            self.client.get(f"/api/v1/platform/organisations/{self.acme.id}/billing/").status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_the_organisation_summary_now_carries_the_plan(self):
        rows = self.client.get("/api/v1/platform/organisations/").data
        self.assertEqual(rows[0]["plan"]["plan"]["code"], "trial")
