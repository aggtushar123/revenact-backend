"""Seats are counted where they are granted, and the count cannot be beaten.

The last test is the one the brief names: one seat left, two administrators
approving at the same moment, exactly one succeeds.
"""

import threading

from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings

from services.accounts.capabilities import ALL_CAPABILITIES
from services.accounts.models import Organisation, Role, User
from services.billing import accounts, seats
from services.billing.models import SeatAssignment
from services.identity import onboarding, ownership
from services.identity.models import AccessRequest, OrganizationDomain, OrganizationMembership
from services.identity.providers import VerifiedIdentity


def verified(email, subject):
    return VerifiedIdentity(
        provider="google", subject=subject, email=email, email_verified=True, name="P"
    )


def make_org(name="Acme Inc", *, seats_limit=None):
    org = Organisation.objects.create(name=name)
    OrganizationDomain.objects.create(
        organisation=org, domain="acme.io", verification_status="verified"
    )
    role = Role.objects.create(
        organisation=org, name="Admin", slug="admin-2", permissions=list(ALL_CAPABILITIES)
    )
    admin = User.objects.create_user(
        email="admin@acme.io", password="x", name="Admin", organisation=org, role=role
    )
    ownership.claim(admin, org)
    member_role = Role.objects.create(
        organisation=org, name="Member", slug="member", permissions=[]
    )
    account = accounts.ensure(org)
    if seats_limit is not None:
        account.seats_limit = seats_limit
        account.save(update_fields=["seats_limit"])
    return org, admin, member_role, account


class TrialTests(TestCase):
    def test_a_new_organisation_gets_the_trial_allowances(self):
        org = Organisation.objects.create(name="Newco")
        account = org.billing_account
        self.assertEqual(account.plan.code, "trial")
        self.assertEqual(account.seats_limit, 3)
        self.assertEqual(account.status, "trialing")
        self.assertIsNotNone(account.trial_ends_at)

    def test_an_active_member_holds_a_seat_and_a_departed_one_does_not(self):
        org, admin, _, account = make_org()
        self.assertEqual(seats.used(account), 1, "the founder")
        membership = OrganizationMembership.objects.get(user=admin)
        membership.status = "revoked"
        membership.save()
        self.assertEqual(seats.used(account), 0)
        self.assertEqual(
            SeatAssignment.objects.filter(membership=membership).count(), 1, "history kept"
        )


class AllocationTests(TestCase):
    def setUp(self):
        self.org, self.admin, self.member_role, self.account = make_org(seats_limit=2)

    def _request(self, email, subject):
        _, row = onboarding.request_access(verified(email, subject))
        return row

    def test_approval_takes_a_seat_and_the_last_refusal_writes_nothing(self):
        first = self._request("one@acme.io", "s1")
        second = self._request("two@acme.io", "s2")

        onboarding.approve(first, reviewer=self.admin, role=self.member_role)
        self.assertEqual(seats.used(self.account), 2)

        with self.assertRaises(onboarding.OnboardingError) as caught:
            onboarding.approve(second, reviewer=self.admin, role=self.member_role)
        self.assertEqual(caught.exception.code, "INSUFFICIENT_SEATS")
        self.assertEqual(seats.used(self.account), 2)
        self.assertFalse(OrganizationMembership.objects.filter(user__email="two@acme.io").exists())
        self.assertEqual(AccessRequest.objects.get(pk=second.pk).status, "pending", "still waiting")
        self.assertIsNone(User.objects.get(email="two@acme.io").organisation)

    def test_an_invitation_takes_a_seat_at_acceptance_not_before(self):
        onboarding.invite(self.org, email="new@acme.io", role=self.member_role, inviter=self.admin)
        onboarding.invite(self.org, email="more@acme.io", role=self.member_role, inviter=self.admin)
        self.assertEqual(seats.used(self.account), 1)

        from services.identity import login

        login.resolve_user(verified("new@acme.io", "n1"))
        self.assertEqual(seats.used(self.account), 2)
        with self.assertRaises(login.LoginError) as caught:
            login.resolve_user(verified("more@acme.io", "n2"))
        self.assertEqual(caught.exception.code, "INSUFFICIENT_SEATS")

    def test_a_freed_seat_can_be_taken_again(self):
        first = self._request("one@acme.io", "s1")
        onboarding.approve(first, reviewer=self.admin, role=self.member_role)
        membership = OrganizationMembership.objects.get(user__email="one@acme.io")
        membership.status = "revoked"
        membership.save()
        second = self._request("two@acme.io", "s2")
        onboarding.approve(second, reviewer=self.admin, role=self.member_role)
        self.assertEqual(seats.used(self.account), 2)

    @override_settings(BILLING_ENFORCED=False)
    def test_enforcement_off_records_but_never_refuses(self):
        for i in range(3):
            row = self._request(f"p{i}@acme.io", f"s{i}")
            onboarding.approve(row, reviewer=self.admin, role=self.member_role)
        self.assertEqual(seats.used(self.account), 4)
        self.assertEqual(self.account.seats_limit, 2)

    def test_staff_cannot_lower_the_allowance_below_the_seats_in_use(self):
        from services.billing.ledger import BillingError

        staff = User.objects.create_superuser(email="s@revenact.io", password="x", name="S")
        first = self._request("one@acme.io", "s1")
        onboarding.approve(first, reviewer=self.admin, role=self.member_role)
        with self.assertRaises(BillingError) as caught:
            accounts.set_seats(self.account, 1, actor=staff, reason="trim")
        self.assertEqual(caught.exception.code, "SEATS_IN_USE")
        accounts.set_seats(self.account, 5, actor=staff, reason="upsell")
        self.assertEqual(self.account.seats_limit, 5)


class ConcurrentApprovalTests(TransactionTestCase):
    """The brief's mandatory test (§71): one seat left, two approvals at the
    same instant, exactly one succeeds. Real threads on real connections,
    serialised by the row lock on the billing account."""

    def test_two_concurrent_approvals_for_the_last_seat_admit_exactly_one(self):
        org, admin, member_role, account = make_org(seats_limit=2)  # founder + one
        first = onboarding.request_access(verified("one@acme.io", "s1"))[1]
        second = onboarding.request_access(verified("two@acme.io", "s2"))[1]

        barrier = threading.Barrier(2)
        outcomes = {}

        def approve(label, row):
            try:
                barrier.wait(timeout=10)
                onboarding.approve(row, reviewer=admin, role=member_role)
                outcomes[label] = "ok"
            except onboarding.OnboardingError as exc:
                outcomes[label] = exc.code
            except Exception as exc:  # noqa: BLE001 - the test must see any failure
                outcomes[label] = f"error: {exc!r}"
            finally:
                connection.close()

        threads = [
            threading.Thread(target=approve, args=(label, row))
            for label, row in (("a", first), ("b", second))
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(sorted(outcomes.values()), ["INSUFFICIENT_SEATS", "ok"], outcomes)
        self.assertEqual(seats.used(account), 2)
        self.assertEqual(
            OrganizationMembership.objects.filter(organisation=org, status="active").count(), 2
        )
        self.assertEqual(
            AccessRequest.objects.filter(organisation=org, status="approved").count(), 1
        )
        self.assertEqual(
            AccessRequest.objects.filter(organisation=org, status="pending").count(), 1
        )
