"""Every existing organisation gets a billing account on the trial, and every
active membership an open seat, so the numbers are true from the first
request. Reversible: the rows go."""

from datetime import timedelta

from django.conf import settings
from django.db import migrations
from django.utils import timezone


def backfill(apps, schema_editor):
    Organisation = apps.get_model("accounts", "Organisation")
    Membership = apps.get_model("identity", "OrganizationMembership")
    Plan = apps.get_model("billing", "Plan")
    BillingAccount = apps.get_model("billing", "BillingAccount")
    CreditLedger = apps.get_model("billing", "CreditLedger")
    SeatAssignment = apps.get_model("billing", "SeatAssignment")

    plan, _ = Plan.objects.get_or_create(
        code="trial",
        defaults={
            "name": "Trial",
            "seats_included": settings.BILLING_TRIAL_SEATS,
            "monthly_credits": settings.BILLING_TRIAL_CREDITS,
            "price_cents": 0,
            "is_trial": True,
            "is_public": False,
            "sort_order": 0,
        },
    )
    for organisation in Organisation.objects.all().iterator():
        account, created = BillingAccount.objects.get_or_create(
            organisation=organisation,
            defaults={
                "plan": plan,
                "status": "trialing",
                # Existing tenants may already hold more than the trial's
                # seats; the allowance starts at whatever they have.
                "seats_limit": max(
                    plan.seats_included,
                    Membership.objects.filter(organisation=organisation, status="active").count(),
                ),
                "trial_ends_at": timezone.now() + timedelta(days=settings.BILLING_TRIAL_DAYS),
            },
        )
        if created and plan.monthly_credits:
            CreditLedger.objects.create(
                account=account,
                kind="grant",
                amount=plan.monthly_credits,
                balance_after=plan.monthly_credits,
                reason=f"Trial: {plan.monthly_credits} credits for {settings.BILLING_TRIAL_DAYS} days",
                reference=f"trial:{organisation.pk}",
            )
        for membership in Membership.objects.filter(organisation=organisation, status="active"):
            SeatAssignment.objects.get_or_create(
                account=account, membership=membership, released_at=None
            )


def unfill(apps, schema_editor):
    for name in ("SeatAssignment", "CreditLedger", "BillingAccount"):
        apps.get_model("billing", name).objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0001_initial"),
        ("identity", "0006_membership_owner"),
    ]

    operations = [migrations.RunPython(backfill, unfill)]
