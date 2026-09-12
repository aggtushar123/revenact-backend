"""Dev/demo convenience — not part of the product. Seeds Risk rows
under existing demo Customers and Accounts so the standalone Pipelines
board's "Risks" tab (react-ts-app's src/pages/pipelines/
PipelinesPage.tsx) has real, per-entity data across every stage — run
after seed_demo_accounts.

Unlike seed_demo_opportunities.py, none of this is lifted from the
mock's own Risk cards — the mock named placeholder companies ("Digital
Operations", "Culinary Innovation Lab (HCIL)", "Pacific Retail
Ventures", "Horizon Analytics Group", "Sunrise Logistics", "EMEA
Operations") that don't exist as real Customers/Accounts anywhere in
this codebase's seed data, so there was nothing real to carry over.
Every entry below is new demo content against real seeded Customers/
Accounts instead, spread across all 4 board columns (Open/Mitigated/
Realised/Abandoned) and covering a mix of org-level and account-level
risks the same way seed_demo_opportunities.py does.

Idempotent: matched by (parent, title), so re-running updates existing
rows instead of duplicating them. Silently skips any customer_name/
account_name that doesn't exist yet in the target organisation.

Usage:
    python manage.py seed_demo_risks --org-email alice@acme.io
"""

from django.core.management.base import BaseCommand, CommandError

from services.accounts.models import User
from services.customers.models import Account, Customer, Risk

# Org-level risks — customer_name must match a Customer.name already
# seeded by seed_demo_customers.
DEMO_CUSTOMER_RISKS = [
    {
        "customer_name": "WeWork",
        "title": "Renewal Risk — Contract Expiry",
        "mrr": "8500.00",
        "stage": "open",
        "priority": "high",
    },
    {
        "customer_name": "Pizza Hut",
        "title": "Disengagement Risk Q1",
        "mrr": "5000.00",
        "stage": "open",
        "priority": "medium",
    },
    {
        "customer_name": "Twilio",
        "title": "Downgrade Risk",
        "mrr": "12000.00",
        "stage": "open",
        "priority": "medium",
    },
    {
        "customer_name": "Zoom",
        "title": "Low Adoption Risk",
        "mrr": "6000.00",
        "stage": "open",
        "priority": "low",
    },
    {
        "customer_name": "Oracle",
        "title": "Executive Sponsor Departure Risk",
        "mrr": "20000.00",
        "stage": "mitigated",
        "priority": "high",
    },
    {
        "customer_name": "Spotify",
        "title": "Support Escalation Risk",
        "mrr": "9500.00",
        "stage": "realised",
        "priority": "high",
    },
    {
        "customer_name": "Uber",
        "title": "Champion Change Risk",
        "mrr": "15000.00",
        "stage": "abandoned",
        "priority": "medium",
    },
]

# Account-level risks — customer_name/account_name must match an
# Account already seeded by seed_demo_accounts (under that customer).
DEMO_ACCOUNT_RISKS = [
    {
        "customer_name": "Kraft Heinz",
        "account_name": "APAC Division",
        "title": "Renewal Risk — Contract Expiry",
        "mrr": "17000.00",
        "stage": "open",
        "priority": "high",
    },
    {
        "customer_name": "Hyatt Hotels Corporation",
        "account_name": "Hyatt Americas",
        "title": "Churn Risk",
        "mrr": "30000.00",
        "stage": "open",
        "priority": "high",
    },
    {
        "customer_name": "Apple Inc",
        "account_name": "Apple APAC",
        "title": "Budget Freeze Risk",
        "mrr": "11000.00",
        "stage": "mitigated",
        "priority": "medium",
    },
    {
        "customer_name": "Salesforce",
        "account_name": "Salesforce Core Platform",
        "title": "Churn Risk",
        "mrr": "20000.00",
        "stage": "realised",
        "priority": "high",
    },
]


#: What a logged risk puts at stake, as a share of what the account pays per
#: month, by how serious someone called it. A high-priority risk threatens most
#: of the contract; a low one is a line item.
#:
#: Scaled rather than literal for the same reason the opportunity seeder is:
#: the standalone figures here were larger than the accounts they hung off, and
#: the Revenue Forecast's worst case came out **negative** — a forecast saying
#: the book will owe money.
RISK_SHARE_OF_ARR = {"high": 0.7, "medium": 0.35, "low": 0.15}

#: For an account with no ARR recorded.
FALLBACK_MRR = 800


def scaled_mrr(parent, priority):
    """Monthly ARR at stake, to the nearest hundred. Deterministic, and never
    more than the account is worth — the forecast caps it anyway, but a seed
    that needs capping is a seed that teaches the wrong shape."""
    annual = float(getattr(parent, "arr_billed_at_account", None) or getattr(parent, "arr", 0) or 0)
    if annual <= 0:
        return FALLBACK_MRR
    monthly = annual / 12
    return round(monthly * RISK_SHARE_OF_ARR.get(priority, 0.35) / 100) * 100


class Command(BaseCommand):
    help = "Seeds demo Risk rows under existing demo Customers/Accounts."

    def add_arguments(self, parser):
        parser.add_argument(
            "--org-email",
            required=True,
            help="Email of a user in the target organisation (e.g. the admin who signed up).",
        )

    def handle(self, *args, **options):
        try:
            caller = User.objects.get(email=options["org_email"])
        except User.DoesNotExist as exc:
            raise CommandError(f"No user with email {options['org_email']!r}.") from exc

        org = caller.organisation
        created, updated, skipped = 0, 0, 0

        for row in DEMO_CUSTOMER_RISKS:
            try:
                customer = Customer.objects.get(organisation=org, name=row["customer_name"])
            except Customer.DoesNotExist:
                self.stderr.write(
                    f"  skipping risk — no customer {row['customer_name']!r} in {org.name}."
                )
                skipped += 1
                continue

            _, was_created = Risk.objects.update_or_create(
                customer=customer,
                title=row["title"],
                defaults={
                    # The literal is ignored in favour of a figure scaled to
                    # this account — see RISK_SHARE_OF_ARR.
                    "mrr": scaled_mrr(customer, row["priority"]),
                    "stage": row["stage"],
                    "priority": row["priority"],
                },
            )
            created += was_created
            updated += not was_created

        for row in DEMO_ACCOUNT_RISKS:
            try:
                account = (
                    Account.objects.filter(
                        customers__organisation=org,
                        customers__name=row["customer_name"],
                        name=row["account_name"],
                    )
                    .distinct()
                    .get()
                )
            except Account.DoesNotExist:
                self.stderr.write(
                    f"  skipping risk — no account {row['account_name']!r} under "
                    f"{row['customer_name']!r} in {org.name}."
                )
                skipped += 1
                continue

            _, was_created = Risk.objects.update_or_create(
                account=account,
                title=row["title"],
                defaults={
                    # See RISK_SHARE_OF_ARR — scaled to the account itself.
                    "mrr": scaled_mrr(account, row["priority"]),
                    "stage": row["stage"],
                    "priority": row["priority"],
                },
            )
            created += was_created
            updated += not was_created

        self.stdout.write(
            self.style.SUCCESS(
                f"{org.name}: created {created}, updated {updated}, skipped {skipped} risk(s)."
            )
        )
