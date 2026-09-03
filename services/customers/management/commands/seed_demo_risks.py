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
                    "mrr": row["mrr"],
                    "stage": row["stage"],
                    "priority": row["priority"],
                },
            )
            created += was_created
            updated += not was_created

        for row in DEMO_ACCOUNT_RISKS:
            try:
                account = Account.objects.get(
                    customer__organisation=org,
                    customer__name=row["customer_name"],
                    name=row["account_name"],
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
                    "mrr": row["mrr"],
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
