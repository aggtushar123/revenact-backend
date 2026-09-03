"""Dev/demo convenience — not part of the product. Seeds Task rows
under existing demo Customers and Accounts so ActivityFeed's "Tasks"
filter has real, per-entity data on both the Organization Details
page (General tab) and the standalone Account page — run after
seed_demo_customers and seed_demo_accounts.

Covers every company seed_demo_customers creates (2-3 org-level tasks
each) and every sub-account seed_demo_accounts creates (1-2
account-level tasks each) — a handful match the original
activityData.ts/accountActivityData.ts mock content for the companies
that mock happened to name; the rest are new demo content, same
reasoning as seed_demo_activities/seed_demo_emails.

Unlike Activity's occurred_at or Email's sent_at (immutable historical
facts, seeded as fixed calendar dates), a Task's due_date is inherently
relative to "now" — the frontend buckets it into Overdue/This Week/
Next Week/Later by comparing it against today. A fixed calendar date
would drift into the wrong bucket (or off the end entirely) the moment
enough real time passes. So due dates here are computed as offsets
from the date this command is actually run, and idempotency is keyed
on (parent, title) rather than (parent, title, due_date) — re-running
this command later refreshes every task's due_date to stay freshly
spread across the buckets instead of drifting stale.

Usage:
    python manage.py seed_demo_tasks --org-email alice@acme.io
"""

import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from services.accounts.models import User
from services.customers.models import Account, Customer, Task

# Org-level tasks — customer_name must match a Customer.name already
# seeded by seed_demo_customers. `due_offset_days` is added to today's
# date at run time (see module docstring) — not a guarantee of landing
# in a specific named bucket, just a realistic spread across overdue/
# upcoming-soon/further-out.
DEMO_CUSTOMER_TASKS = [
    {
        "customer_name": "Apple Inc",
        "title": "Prepare QBR deck for Q1",
        "assignee_name": "Edgar Holmes",
        "due_offset_days": 2,
        "priority": "high",
        "status": "in-progress",
    },
    {
        "customer_name": "Apple Inc",
        "title": "Schedule renewal discussion call",
        "assignee_name": "Natalie Reyes",
        "due_offset_days": -5,
        "priority": "high",
        "status": "pending",
    },
    {
        "customer_name": "Apple Inc",
        "title": "Update customer health scorecard",
        "assignee_name": "Edgar Holmes",
        "due_offset_days": 9,
        "priority": "medium",
        "status": "pending",
    },
    {
        "customer_name": "Apple Inc",
        "title": "Send product update newsletter",
        "assignee_name": "Sarah Chen",
        "due_offset_days": 25,
        "priority": "low",
        "status": "completed",
    },
    {
        "customer_name": "Pizza Hut",
        "title": "Resolve integration escalation",
        "assignee_name": "Edgar Holmes",
        "due_offset_days": -3,
        "priority": "high",
        "status": "in-progress",
    },
    {
        "customer_name": "Pizza Hut",
        "title": "Create onboarding checklist",
        "assignee_name": "Sarah Chen",
        "due_offset_days": 4,
        "priority": "medium",
        "status": "pending",
    },
    {
        "customer_name": "Kraft Heinz",
        "title": "Review success plan KPIs",
        "assignee_name": "Edgar Holmes",
        "due_offset_days": 1,
        "priority": "medium",
        "status": "in-progress",
    },
    {
        "customer_name": "Arista Networks",
        "title": "Prepare Q3 network benchmark report",
        "assignee_name": "Natalie Reyes",
        "due_offset_days": 6,
        "priority": "medium",
        "status": "pending",
    },
    {
        "customer_name": "Arista Networks",
        "title": "Follow up on onboarding milestone",
        "assignee_name": "Edgar Holmes",
        "due_offset_days": -2,
        "priority": "low",
        "status": "completed",
    },
    {
        "customer_name": "Hyatt Hotels Corporation",
        "title": "Investigate booking API downtime root cause",
        "assignee_name": "Edgar Holmes",
        "due_offset_days": -1,
        "priority": "high",
        "status": "in-progress",
    },
    {
        "customer_name": "Hyatt Hotels Corporation",
        "title": "Send July health check summary",
        "assignee_name": "Sarah Chen",
        "due_offset_days": 5,
        "priority": "medium",
        "status": "pending",
    },
    {
        "customer_name": "Notion Labs",
        "title": "Schedule onboarding kickoff call",
        "assignee_name": "Edgar Holmes",
        "due_offset_days": 3,
        "priority": "high",
        "status": "pending",
    },
    {
        "customer_name": "Notion Labs",
        "title": "Log workspace analytics feature request",
        "assignee_name": "Natalie Reyes",
        "due_offset_days": 12,
        "priority": "low",
        "status": "pending",
    },
    {
        "customer_name": "Oracle",
        "title": "Prepare executive alignment recap",
        "assignee_name": "Edgar Holmes",
        "due_offset_days": -4,
        "priority": "medium",
        "status": "completed",
    },
    {
        "customer_name": "Oracle",
        "title": "Review June usage report",
        "assignee_name": "Sarah Chen",
        "due_offset_days": 7,
        "priority": "low",
        "status": "pending",
    },
    {
        "customer_name": "Salesforce",
        "title": "Draft H2 success plan",
        "assignee_name": "Edgar Holmes",
        "due_offset_days": 2,
        "priority": "high",
        "status": "in-progress",
    },
    {
        "customer_name": "Salesforce",
        "title": "Complete health check review",
        "assignee_name": "Sarah Chen",
        "due_offset_days": -6,
        "priority": "medium",
        "status": "completed",
    },
    {
        "customer_name": "Shopify",
        "title": "Submit renewal proposal",
        "assignee_name": "Edgar Holmes",
        "due_offset_days": 1,
        "priority": "high",
        "status": "in-progress",
    },
    {
        "customer_name": "Shopify",
        "title": "Send enablement session recording",
        "assignee_name": "Natalie Reyes",
        "due_offset_days": 10,
        "priority": "low",
        "status": "pending",
    },
    {
        "customer_name": "Spotify",
        "title": "Share quarterly ROI summary",
        "assignee_name": "Edgar Holmes",
        "due_offset_days": 4,
        "priority": "medium",
        "status": "pending",
    },
    {
        "customer_name": "Spotify",
        "title": "Confirm week 1 onboarding setup",
        "assignee_name": "Natalie Reyes",
        "due_offset_days": -8,
        "priority": "low",
        "status": "completed",
    },
    {
        "customer_name": "Stripe",
        "title": "Review API call volume spike",
        "assignee_name": "Sarah Chen",
        "due_offset_days": 0,
        "priority": "high",
        "status": "in-progress",
    },
    {
        "customer_name": "Stripe",
        "title": "Escalate payment webhook delays",
        "assignee_name": "Edgar Holmes",
        "due_offset_days": -2,
        "priority": "high",
        "status": "in-progress",
    },
    {
        "customer_name": "Twilio",
        "title": "Complete July health check review",
        "assignee_name": "Sarah Chen",
        "due_offset_days": 8,
        "priority": "medium",
        "status": "pending",
    },
    {
        "customer_name": "Twilio",
        "title": "Update success plan for Q3",
        "assignee_name": "Edgar Holmes",
        "due_offset_days": 15,
        "priority": "low",
        "status": "pending",
    },
    {
        "customer_name": "Uber",
        "title": "Recap executive alignment session",
        "assignee_name": "Edgar Holmes",
        "due_offset_days": -3,
        "priority": "medium",
        "status": "completed",
    },
    {
        "customer_name": "Uber",
        "title": "Send adoption wins summary",
        "assignee_name": "Natalie Reyes",
        "due_offset_days": 6,
        "priority": "low",
        "status": "pending",
    },
    {
        "customer_name": "WeWork",
        "title": "Investigate billing discrepancy",
        "assignee_name": "Edgar Holmes",
        "due_offset_days": -1,
        "priority": "high",
        "status": "in-progress",
    },
    {
        "customer_name": "WeWork",
        "title": "Review health check churn signals",
        "assignee_name": "Sarah Chen",
        "due_offset_days": 3,
        "priority": "high",
        "status": "pending",
    },
    {
        "customer_name": "Zoom",
        "title": "Review August usage analysis",
        "assignee_name": "Sarah Chen",
        "due_offset_days": 5,
        "priority": "low",
        "status": "pending",
    },
    {
        "customer_name": "Zoom",
        "title": "Confirm final onboarding milestone",
        "assignee_name": "Natalie Reyes",
        "due_offset_days": -7,
        "priority": "medium",
        "status": "completed",
    },
]

# Account-level tasks — customer_name/account_name must match an
# Account already seeded by seed_demo_accounts (under that customer).
DEMO_ACCOUNT_TASKS = [
    {
        "customer_name": "Apple Inc",
        "account_name": "North America Enterprise",
        "title": "Prepare account success plan for Q2",
        "assignee_name": "Edgar Holmes",
        "due_offset_days": 2,
        "priority": "high",
        "status": "in-progress",
    },
    {
        "customer_name": "Apple Inc",
        "account_name": "North America Enterprise",
        "title": "Schedule executive alignment call",
        "assignee_name": "Natalie Reyes",
        "due_offset_days": 9,
        "priority": "medium",
        "status": "pending",
    },
    {
        "customer_name": "Apple Inc",
        "account_name": "Apple EMEA",
        "title": "Draft renewal commercial terms",
        "assignee_name": "Edgar Holmes",
        "due_offset_days": -4,
        "priority": "high",
        "status": "in-progress",
    },
    {
        "customer_name": "Apple Inc",
        "account_name": "Apple APAC",
        "title": "Confirm APAC rollout milestone",
        "assignee_name": "Natalie Reyes",
        "due_offset_days": 5,
        "priority": "medium",
        "status": "pending",
    },
    {
        "customer_name": "Kraft Heinz",
        "account_name": "APAC Division",
        "title": "Review APAC usage uptick",
        "assignee_name": "Sarah Chen",
        "due_offset_days": -2,
        "priority": "low",
        "status": "completed",
    },
    {
        "customer_name": "Kraft Heinz",
        "account_name": "Heinz Europe",
        "title": "Confirm renewal timeline with Europe leadership",
        "assignee_name": "Edgar Holmes",
        "due_offset_days": 6,
        "priority": "medium",
        "status": "pending",
    },
    {
        "customer_name": "Kraft Heinz",
        "account_name": "Kraft Heinz North America (Renamed)",
        "title": "Send North America adoption summary",
        "assignee_name": "Edgar Holmes",
        "due_offset_days": 11,
        "priority": "low",
        "status": "pending",
    },
    {
        "customer_name": "Arista Networks",
        "account_name": "Arista Global",
        "title": "Resolve data sync delay",
        "assignee_name": "Natalie Reyes",
        "due_offset_days": -1,
        "priority": "high",
        "status": "in-progress",
    },
    {
        "customer_name": "Hyatt Hotels Corporation",
        "account_name": "Hyatt Americas",
        "title": "Finalize Americas renewal proposal",
        "assignee_name": "Edgar Holmes",
        "due_offset_days": 3,
        "priority": "medium",
        "status": "pending",
    },
    {
        "customer_name": "Hyatt Hotels Corporation",
        "account_name": "Hyatt EMEA & APAC",
        "title": "Confirm EMEA & APAC onboarding milestone",
        "assignee_name": "Natalie Reyes",
        "due_offset_days": 8,
        "priority": "low",
        "status": "pending",
    },
    {
        "customer_name": "Oracle",
        "account_name": "Oracle Cloud Division",
        "title": "Escalate cloud migration delay",
        "assignee_name": "Edgar Holmes",
        "due_offset_days": -3,
        "priority": "high",
        "status": "in-progress",
    },
    {
        "customer_name": "Pizza Hut",
        "account_name": "Pizza Hut International",
        "title": "Align with international leadership on Q3 goals",
        "assignee_name": "Edgar Holmes",
        "due_offset_days": 7,
        "priority": "medium",
        "status": "pending",
    },
    {
        "customer_name": "Pizza Hut",
        "account_name": "Pizza Hut US Operations",
        "title": "Confirm US Operations onboarding milestone",
        "assignee_name": "Natalie Reyes",
        "due_offset_days": -5,
        "priority": "low",
        "status": "completed",
    },
    {
        "customer_name": "Salesforce",
        "account_name": "Salesforce Core Platform",
        "title": "Update Core Platform success plan for H2",
        "assignee_name": "Edgar Holmes",
        "due_offset_days": 4,
        "priority": "medium",
        "status": "pending",
    },
    {
        "customer_name": "Shopify",
        "account_name": "Shopify Plus",
        "title": "Resolve checkout API errors",
        "assignee_name": "Sarah Chen",
        "due_offset_days": -1,
        "priority": "high",
        "status": "in-progress",
    },
    {
        "customer_name": "Spotify",
        "account_name": "Spotify Business",
        "title": "Confirm Business tier onboarding milestone",
        "assignee_name": "Natalie Reyes",
        "due_offset_days": 10,
        "priority": "low",
        "status": "pending",
    },
    {
        "customer_name": "Stripe",
        "account_name": "Stripe Payments",
        "title": "Share Payments account adoption wins",
        "assignee_name": "Natalie Reyes",
        "due_offset_days": 6,
        "priority": "medium",
        "status": "pending",
    },
    {
        "customer_name": "WeWork",
        "account_name": "WeWork US",
        "title": "Align with WeWork US leadership on renewal path",
        "assignee_name": "Edgar Holmes",
        "due_offset_days": 2,
        "priority": "medium",
        "status": "pending",
    },
]


class Command(BaseCommand):
    help = "Seeds demo Task rows under existing demo Customers/Accounts."

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
        today = timezone.now().date()
        created, updated, skipped = 0, 0, 0

        for row in DEMO_CUSTOMER_TASKS:
            try:
                customer = Customer.objects.get(organisation=org, name=row["customer_name"])
            except Customer.DoesNotExist:
                self.stderr.write(
                    f"  skipping task — no customer {row['customer_name']!r} in {org.name}."
                )
                skipped += 1
                continue

            due_date = today + datetime.timedelta(days=row["due_offset_days"])
            _, was_created = Task.objects.update_or_create(
                customer=customer,
                title=row["title"],
                defaults={
                    "assignee_name": row["assignee_name"],
                    "due_date": due_date,
                    "priority": row["priority"],
                    "status": row["status"],
                },
            )
            created += was_created
            updated += not was_created

        for row in DEMO_ACCOUNT_TASKS:
            try:
                account = Account.objects.filter(
                    customers__organisation=org,
                    customers__name=row["customer_name"],
                    name=row["account_name"],
                ).distinct().get()
            except Account.DoesNotExist:
                self.stderr.write(
                    f"  skipping task — no account {row['account_name']!r} under "
                    f"{row['customer_name']!r} in {org.name}."
                )
                skipped += 1
                continue

            due_date = today + datetime.timedelta(days=row["due_offset_days"])
            _, was_created = Task.objects.update_or_create(
                account=account,
                title=row["title"],
                defaults={
                    "assignee_name": row["assignee_name"],
                    "due_date": due_date,
                    "priority": row["priority"],
                    "status": row["status"],
                },
            )
            created += was_created
            updated += not was_created

        self.stdout.write(
            self.style.SUCCESS(
                f"{org.name}: created {created}, updated {updated}, "
                f"skipped {skipped} task(s)."
            )
        )
