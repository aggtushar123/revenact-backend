"""Dev/demo convenience — not part of the product. Seeds Activity rows
(from activityData.ts/accountActivityData.ts's mocks) under existing demo
Customers and Accounts so ActivityFeed's "Activities" filter has real,
per-entity data on both the Organization Details page (General tab) and
the standalone Account page — run after seed_demo_customers and
seed_demo_accounts.

Idempotent: matched by (parent, type, occurred_at), so re-running updates
existing rows instead of duplicating them. Silently skips any
customer_name/account_name that doesn't exist yet in the target
organisation.

Usage:
    python manage.py seed_demo_activities --org-email alice@acme.io
"""

from django.core.management.base import BaseCommand, CommandError

from services.accounts.models import User
from services.customers.models import Account, Activity, Customer

# Org-level activities — customer_name must match a Customer.name already
# seeded by seed_demo_customers.
DEMO_CUSTOMER_ACTIVITIES = [
    {
        "customer_name": "Apple Inc",
        "type": "value_reinforcement",
        "occurred_at": "2026-03-05",
        "links": 1,
        "watchers": 1,
    },
    {
        "customer_name": "Apple Inc",
        "type": "enablement_retraining",
        "occurred_at": "2026-03-05",
        "links": 1,
        "watchers": 0,
    },
    {
        "customer_name": "Apple Inc",
        "type": "health_check_review",
        "occurred_at": "2026-02-28",
        "links": 2,
        "watchers": 3,
    },
    {
        "customer_name": "Apple Inc",
        "type": "product_usage_analysis",
        "occurred_at": "2026-02-20",
        "links": 0,
        "watchers": 1,
    },
    {
        "customer_name": "Pizza Hut",
        "type": "escalation_triggered",
        "occurred_at": "2026-03-02",
        "links": 1,
        "watchers": 5,
    },
    {
        "customer_name": "Pizza Hut",
        "type": "onboarding_milestone",
        "occurred_at": "2026-02-20",
        "links": 0,
        "watchers": 2,
    },
    {
        "customer_name": "Kraft Heinz",
        "type": "success_plan_created",
        "occurred_at": "2026-03-01",
        "links": 2,
        "watchers": 1,
    },
]

# Account-level activities — customer_name/account_name must match an
# Account already seeded by seed_demo_accounts (under that customer).
DEMO_ACCOUNT_ACTIVITIES = [
    {
        "customer_name": "Apple Inc",
        "account_name": "North America Enterprise",
        "type": "success_plan_updated",
        "occurred_at": "2026-03-28",
        "links": 1,
        "watchers": 2,
    },
    {
        "customer_name": "Apple Inc",
        "account_name": "North America Enterprise",
        "type": "executive_alignment_session",
        "occurred_at": "2026-03-20",
        "links": 0,
        "watchers": 3,
    },
    {
        "customer_name": "Apple Inc",
        "account_name": "North America Enterprise",
        "type": "health_check_review",
        "occurred_at": "2026-03-10",
        "links": 2,
        "watchers": 1,
    },
    {
        "customer_name": "Apple Inc",
        "account_name": "Apple EMEA",
        "type": "renewal_proposal_submitted",
        "occurred_at": "2026-03-15",
        "links": 1,
        "watchers": 4,
    },
]


class Command(BaseCommand):
    help = "Seeds demo Activity rows (from activityData.ts) under existing demo Customers/Accounts."

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

        for row in DEMO_CUSTOMER_ACTIVITIES:
            try:
                customer = Customer.objects.get(organisation=org, name=row["customer_name"])
            except Customer.DoesNotExist:
                self.stderr.write(
                    f"  skipping activity — no customer {row['customer_name']!r} in {org.name}."
                )
                skipped += 1
                continue

            _, was_created = Activity.objects.update_or_create(
                customer=customer,
                type=row["type"],
                occurred_at=row["occurred_at"],
                defaults={"links": row["links"], "watchers": row["watchers"]},
            )
            created += was_created
            updated += not was_created

        for row in DEMO_ACCOUNT_ACTIVITIES:
            try:
                account = Account.objects.get(
                    customer__organisation=org,
                    customer__name=row["customer_name"],
                    name=row["account_name"],
                )
            except Account.DoesNotExist:
                self.stderr.write(
                    f"  skipping activity — no account {row['account_name']!r} under "
                    f"{row['customer_name']!r} in {org.name}."
                )
                skipped += 1
                continue

            _, was_created = Activity.objects.update_or_create(
                account=account,
                type=row["type"],
                occurred_at=row["occurred_at"],
                defaults={"links": row["links"], "watchers": row["watchers"]},
            )
            created += was_created
            updated += not was_created

        self.stdout.write(
            self.style.SUCCESS(
                f"{org.name}: created {created}, updated {updated}, "
                f"skipped {skipped} activity(ies)."
            )
        )
