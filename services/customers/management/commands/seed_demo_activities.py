"""Dev/demo convenience — not part of the product. Seeds Activity rows
under existing demo Customers and Accounts so ActivityFeed's
"Activities" filter has real, per-entity data on both the Organization
Details page (General tab) and the standalone Account page — run after
seed_demo_customers and seed_demo_accounts.

Covers every company seed_demo_customers creates (2-3 org-level
activities each) and every sub-account seed_demo_accounts creates (2
account-level activities each) — a handful of these match the
original activityData.ts/accountActivityData.ts mock content for the
companies that mock happened to name (Apple Inc, Pizza Hut, Kraft
Heinz and two of Apple's sub-accounts); the rest are new demo content
invented to give every other seeded company/account something to show
too, so the frontend can be verified end to end instead of mostly
showing "No activities found".

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
    {
        "customer_name": "Arista Networks",
        "type": "product_usage_analysis",
        "occurred_at": "2026-06-10",
        "links": 1,
        "watchers": 2,
    },
    {
        "customer_name": "Arista Networks",
        "type": "onboarding_milestone",
        "occurred_at": "2026-05-02",
        "links": 0,
        "watchers": 1,
    },
    {
        "customer_name": "Hyatt Hotels Corporation",
        "type": "escalation_triggered",
        "occurred_at": "2026-07-18",
        "links": 2,
        "watchers": 6,
    },
    {
        "customer_name": "Hyatt Hotels Corporation",
        "type": "health_check_review",
        "occurred_at": "2026-06-01",
        "links": 1,
        "watchers": 2,
    },
    {
        "customer_name": "Hyatt Hotels Corporation",
        "type": "success_plan_updated",
        "occurred_at": "2026-04-20",
        "links": 0,
        "watchers": 1,
    },
    {
        "customer_name": "Notion Labs",
        "type": "onboarding_milestone",
        "occurred_at": "2026-08-05",
        "links": 0,
        "watchers": 3,
    },
    {
        "customer_name": "Notion Labs",
        "type": "value_reinforcement",
        "occurred_at": "2026-07-22",
        "links": 1,
        "watchers": 1,
    },
    {
        "customer_name": "Oracle",
        "type": "executive_alignment_session",
        "occurred_at": "2026-06-28",
        "links": 1,
        "watchers": 4,
    },
    {
        "customer_name": "Oracle",
        "type": "product_usage_analysis",
        "occurred_at": "2026-05-15",
        "links": 0,
        "watchers": 2,
    },
    {
        "customer_name": "Salesforce",
        "type": "success_plan_created",
        "occurred_at": "2026-07-01",
        "links": 2,
        "watchers": 3,
    },
    {
        "customer_name": "Salesforce",
        "type": "health_check_review",
        "occurred_at": "2026-05-20",
        "links": 1,
        "watchers": 1,
    },
    {
        "customer_name": "Shopify",
        "type": "renewal_proposal_submitted",
        "occurred_at": "2026-08-12",
        "links": 1,
        "watchers": 5,
    },
    {
        "customer_name": "Shopify",
        "type": "enablement_retraining",
        "occurred_at": "2026-06-30",
        "links": 0,
        "watchers": 1,
    },
    {
        "customer_name": "Spotify",
        "type": "value_reinforcement",
        "occurred_at": "2026-07-09",
        "links": 1,
        "watchers": 2,
    },
    {
        "customer_name": "Spotify",
        "type": "onboarding_milestone",
        "occurred_at": "2026-04-11",
        "links": 0,
        "watchers": 0,
    },
    {
        "customer_name": "Stripe",
        "type": "product_usage_analysis",
        "occurred_at": "2026-08-01",
        "links": 2,
        "watchers": 3,
    },
    {
        "customer_name": "Stripe",
        "type": "escalation_triggered",
        "occurred_at": "2026-06-14",
        "links": 1,
        "watchers": 7,
    },
    {
        "customer_name": "Twilio",
        "type": "health_check_review",
        "occurred_at": "2026-07-25",
        "links": 1,
        "watchers": 2,
    },
    {
        "customer_name": "Twilio",
        "type": "success_plan_updated",
        "occurred_at": "2026-05-30",
        "links": 0,
        "watchers": 1,
    },
    {
        "customer_name": "Uber",
        "type": "executive_alignment_session",
        "occurred_at": "2026-08-18",
        "links": 1,
        "watchers": 3,
    },
    {
        "customer_name": "Uber",
        "type": "value_reinforcement",
        "occurred_at": "2026-06-05",
        "links": 0,
        "watchers": 1,
    },
    {
        "customer_name": "WeWork",
        "type": "escalation_triggered",
        "occurred_at": "2026-07-02",
        "links": 2,
        "watchers": 5,
    },
    {
        "customer_name": "WeWork",
        "type": "health_check_review",
        "occurred_at": "2026-05-08",
        "links": 1,
        "watchers": 2,
    },
    {
        "customer_name": "Zoom",
        "type": "product_usage_analysis",
        "occurred_at": "2026-08-20",
        "links": 1,
        "watchers": 2,
    },
    {
        "customer_name": "Zoom",
        "type": "onboarding_milestone",
        "occurred_at": "2026-06-22",
        "links": 0,
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
    {
        "customer_name": "Apple Inc",
        "account_name": "Apple APAC",
        "type": "onboarding_milestone",
        "occurred_at": "2026-06-19",
        "links": 0,
        "watchers": 1,
    },
    {
        "customer_name": "Apple Inc",
        "account_name": "Apple APAC",
        "type": "value_reinforcement",
        "occurred_at": "2026-04-30",
        "links": 1,
        "watchers": 2,
    },
    {
        "customer_name": "Kraft Heinz",
        "account_name": "APAC Division",
        "type": "product_usage_analysis",
        "occurred_at": "2026-07-14",
        "links": 1,
        "watchers": 2,
    },
    {
        "customer_name": "Kraft Heinz",
        "account_name": "APAC Division",
        "type": "health_check_review",
        "occurred_at": "2026-05-25",
        "links": 0,
        "watchers": 1,
    },
    {
        "customer_name": "Kraft Heinz",
        "account_name": "Heinz Europe",
        "type": "executive_alignment_session",
        "occurred_at": "2026-08-03",
        "links": 1,
        "watchers": 2,
    },
    {
        "customer_name": "Kraft Heinz",
        "account_name": "Heinz Europe",
        "type": "health_check_review",
        "occurred_at": "2026-06-16",
        "links": 0,
        "watchers": 1,
    },
    {
        "customer_name": "Kraft Heinz",
        "account_name": "Kraft Heinz North America (Renamed)",
        "type": "value_reinforcement",
        "occurred_at": "2026-07-11",
        "links": 1,
        "watchers": 1,
    },
    {
        "customer_name": "Kraft Heinz",
        "account_name": "Kraft Heinz North America (Renamed)",
        "type": "health_check_review",
        "occurred_at": "2026-05-18",
        "links": 0,
        "watchers": 2,
    },
    {
        "customer_name": "Arista Networks",
        "account_name": "Arista Global",
        "type": "success_plan_created",
        "occurred_at": "2026-07-28",
        "links": 2,
        "watchers": 3,
    },
    {
        "customer_name": "Arista Networks",
        "account_name": "Arista Global",
        "type": "escalation_triggered",
        "occurred_at": "2026-05-12",
        "links": 1,
        "watchers": 4,
    },
    {
        "customer_name": "Hyatt Hotels Corporation",
        "account_name": "Hyatt Americas",
        "type": "renewal_proposal_submitted",
        "occurred_at": "2026-07-20",
        "links": 1,
        "watchers": 3,
    },
    {
        "customer_name": "Hyatt Hotels Corporation",
        "account_name": "Hyatt Americas",
        "type": "product_usage_analysis",
        "occurred_at": "2026-05-04",
        "links": 0,
        "watchers": 1,
    },
    {
        "customer_name": "Hyatt Hotels Corporation",
        "account_name": "Hyatt EMEA & APAC",
        "type": "success_plan_updated",
        "occurred_at": "2026-08-09",
        "links": 1,
        "watchers": 2,
    },
    {
        "customer_name": "Hyatt Hotels Corporation",
        "account_name": "Hyatt EMEA & APAC",
        "type": "onboarding_milestone",
        "occurred_at": "2026-06-24",
        "links": 0,
        "watchers": 1,
    },
    {
        "customer_name": "Oracle",
        "account_name": "Oracle Cloud Division",
        "type": "escalation_triggered",
        "occurred_at": "2026-08-14",
        "links": 2,
        "watchers": 5,
    },
    {
        "customer_name": "Oracle",
        "account_name": "Oracle Cloud Division",
        "type": "success_plan_created",
        "occurred_at": "2026-06-08",
        "links": 1,
        "watchers": 2,
    },
    {
        "customer_name": "Pizza Hut",
        "account_name": "Pizza Hut International",
        "type": "product_usage_analysis",
        "occurred_at": "2026-07-06",
        "links": 0,
        "watchers": 1,
    },
    {
        "customer_name": "Pizza Hut",
        "account_name": "Pizza Hut International",
        "type": "executive_alignment_session",
        "occurred_at": "2026-05-27",
        "links": 1,
        "watchers": 3,
    },
    {
        "customer_name": "Pizza Hut",
        "account_name": "Pizza Hut US Operations",
        "type": "onboarding_milestone",
        "occurred_at": "2026-08-22",
        "links": 0,
        "watchers": 2,
    },
    {
        "customer_name": "Pizza Hut",
        "account_name": "Pizza Hut US Operations",
        "type": "value_reinforcement",
        "occurred_at": "2026-06-13",
        "links": 1,
        "watchers": 1,
    },
    {
        "customer_name": "Salesforce",
        "account_name": "Salesforce Core Platform",
        "type": "health_check_review",
        "occurred_at": "2026-07-30",
        "links": 1,
        "watchers": 2,
    },
    {
        "customer_name": "Salesforce",
        "account_name": "Salesforce Core Platform",
        "type": "success_plan_updated",
        "occurred_at": "2026-05-09",
        "links": 0,
        "watchers": 1,
    },
    {
        "customer_name": "Shopify",
        "account_name": "Shopify Plus",
        "type": "renewal_proposal_submitted",
        "occurred_at": "2026-08-06",
        "links": 2,
        "watchers": 4,
    },
    {
        "customer_name": "Shopify",
        "account_name": "Shopify Plus",
        "type": "escalation_triggered",
        "occurred_at": "2026-06-27",
        "links": 1,
        "watchers": 3,
    },
    {
        "customer_name": "Spotify",
        "account_name": "Spotify Business",
        "type": "product_usage_analysis",
        "occurred_at": "2026-07-17",
        "links": 1,
        "watchers": 2,
    },
    {
        "customer_name": "Spotify",
        "account_name": "Spotify Business",
        "type": "onboarding_milestone",
        "occurred_at": "2026-05-01",
        "links": 0,
        "watchers": 1,
    },
    {
        "customer_name": "Stripe",
        "account_name": "Stripe Payments",
        "type": "success_plan_created",
        "occurred_at": "2026-08-11",
        "links": 2,
        "watchers": 3,
    },
    {
        "customer_name": "Stripe",
        "account_name": "Stripe Payments",
        "type": "value_reinforcement",
        "occurred_at": "2026-06-02",
        "links": 1,
        "watchers": 1,
    },
    {
        "customer_name": "WeWork",
        "account_name": "WeWork US",
        "type": "executive_alignment_session",
        "occurred_at": "2026-07-24",
        "links": 1,
        "watchers": 2,
    },
    {
        "customer_name": "WeWork",
        "account_name": "WeWork US",
        "type": "health_check_review",
        "occurred_at": "2026-05-15",
        "links": 0,
        "watchers": 1,
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
