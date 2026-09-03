"""Dev/demo convenience — not part of the product. Seeds Opportunity
rows under existing demo Customers and Accounts so the standalone
Pipelines board's "Opportunities" tab (react-ts-app's
src/pages/pipelines/PipelinesPage.tsx) has real, per-entity data across
every stage — run after seed_demo_accounts.

DEMO_CUSTOMER_OPPORTUNITIES' first 3 entries and DEMO_ACCOUNT_OPPORTUNITIES'
first 2 are field-for-field the original PipelineCard mock's own cards,
for the handful of companies that mock happened to name that actually
exist in our seed (Apple Inc, Apple EMEA, Shopify, Salesforce) — same
reasoning as every other seed_demo_* command this session. The mock's
own remaining cards named companies that don't exist here at all (IBM,
Basecamp, GreenLeaf Organics, EOS Software, Mailchimp) — rather than
force-fitting those onto an unrelated real company, the rest of this
file's entries are new demo content covering more of the companies
seed_demo_customers/seed_demo_accounts actually create, spread across
every stage so the board's own 6 columns all have real cards to show.

Idempotent: matched by (parent, title), so re-running updates existing
rows instead of duplicating them. Silently skips any customer_name/
account_name that doesn't exist yet in the target organisation.

Usage:
    python manage.py seed_demo_opportunities --org-email alice@acme.io
"""

from django.core.management.base import BaseCommand, CommandError

from services.accounts.models import User
from services.customers.models import Account, Customer, Opportunity

# Org-level opportunities — customer_name must match a Customer.name
# already seeded by seed_demo_customers.
DEMO_CUSTOMER_OPPORTUNITIES = [
    {
        "customer_name": "Apple Inc",
        "title": "Monthly Upsell Opportunity",
        "mrr": "10000.00",
        "stage": "solution_validation",
        "priority": "medium",
    },
    {
        "customer_name": "Salesforce",
        "title": "Enterprise Platform Expansion Q4",
        "mrr": "45000.00",
        "stage": "closed_won",
        "priority": "high",
    },
    {
        "customer_name": "Shopify",
        "title": "Digital First Account Expansion",
        "mrr": "18500.00",
        "stage": "discovery",
        "priority": "low",
    },
    {
        "customer_name": "Oracle",
        "title": "Cloud Migration Expansion",
        "mrr": "32000.00",
        "stage": "qualification",
        "priority": "high",
    },
    {
        "customer_name": "Stripe",
        "title": "Payments API Upsell",
        "mrr": "22000.00",
        "stage": "negotiation",
        "priority": "medium",
    },
    {
        "customer_name": "Twilio",
        "title": "SMS Volume Tier Upgrade",
        "mrr": "14000.00",
        "stage": "discovery",
        "priority": "medium",
    },
    {
        "customer_name": "Kraft Heinz",
        "title": "Global Rollout Expansion",
        "mrr": "27500.00",
        "stage": "proposal_price_review",
        "priority": "high",
    },
    {
        "customer_name": "Zoom",
        "title": "Webinar Add-On Package",
        "mrr": "9000.00",
        "stage": "solution_validation",
        "priority": "low",
    },
    {
        "customer_name": "Uber",
        "title": "Multi-Region Expansion Deal",
        "mrr": "38000.00",
        "stage": "negotiation",
        "priority": "high",
    },
]

# Account-level opportunities — customer_name/account_name must match
# an Account already seeded by seed_demo_accounts (under that customer).
DEMO_ACCOUNT_OPPORTUNITIES = [
    {
        "customer_name": "Apple Inc",
        "account_name": "Apple EMEA",
        "title": "Renewal Expansion Opportunity",
        "mrr": "30000.00",
        "stage": "qualification",
        "priority": "high",
    },
    {
        "customer_name": "Apple Inc",
        "account_name": "Apple EMEA",
        "title": "December 2025 – Renewal Expansion Opportunity",
        "mrr": "25000.00",
        "stage": "proposal_price_review",
        "priority": "high",
    },
    {
        "customer_name": "Apple Inc",
        "account_name": "North America Enterprise",
        "title": "Seat Expansion — Q3 Rollout",
        "mrr": "16000.00",
        "stage": "discovery",
        "priority": "medium",
    },
    {
        "customer_name": "Salesforce",
        "account_name": "Salesforce Core Platform",
        "title": "Core Platform Add-On Upsell",
        "mrr": "12000.00",
        "stage": "solution_validation",
        "priority": "medium",
    },
    {
        "customer_name": "Shopify",
        "account_name": "Shopify Plus",
        "title": "Plus Tier Renewal Expansion",
        "mrr": "19500.00",
        "stage": "negotiation",
        "priority": "low",
    },
    {
        "customer_name": "Stripe",
        "account_name": "Stripe Payments",
        "title": "Payments Volume Tier Upsell",
        "mrr": "21000.00",
        "stage": "closed_won",
        "priority": "medium",
    },
    {
        "customer_name": "Hyatt Hotels Corporation",
        "account_name": "Hyatt EMEA & APAC",
        "title": "Regional Expansion Proposal",
        "mrr": "17000.00",
        "stage": "proposal_price_review",
        "priority": "medium",
    },
]


class Command(BaseCommand):
    help = "Seeds demo Opportunity rows under existing demo Customers/Accounts."

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

        for row in DEMO_CUSTOMER_OPPORTUNITIES:
            try:
                customer = Customer.objects.get(organisation=org, name=row["customer_name"])
            except Customer.DoesNotExist:
                self.stderr.write(
                    f"  skipping opportunity — no customer {row['customer_name']!r} in {org.name}."
                )
                skipped += 1
                continue

            _, was_created = Opportunity.objects.update_or_create(
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

        for row in DEMO_ACCOUNT_OPPORTUNITIES:
            try:
                account = Account.objects.filter(
                    customers__organisation=org,
                    customers__name=row["customer_name"],
                    name=row["account_name"],
                ).distinct().get()
            except Account.DoesNotExist:
                self.stderr.write(
                    f"  skipping opportunity — no account {row['account_name']!r} under "
                    f"{row['customer_name']!r} in {org.name}."
                )
                skipped += 1
                continue

            _, was_created = Opportunity.objects.update_or_create(
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
                f"{org.name}: created {created}, updated {updated}, "
                f"skipped {skipped} opportunit(y/ies)."
            )
        )
