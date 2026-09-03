"""Dev/demo convenience — not part of the product. Seeds CalendarEvent
rows under existing demo Customers and Accounts so ActivityFeed's
"Calendar Events" filter has real, per-entity data on both the
Organization Details page (General tab) and the standalone Account
page — run after seed_demo_customers and seed_demo_accounts.

Covers every company seed_demo_customers creates (1-3 org-level events
each) and every sub-account seed_demo_accounts creates (1-2
account-level events each) — a handful match the original
activityData.ts/accountActivityData.ts mock content for the companies
that mock happened to name; the rest are new demo content, same
reasoning as seed_demo_activities/seed_demo_emails/seed_demo_tasks/
seed_demo_notes/seed_demo_tickets.

Idempotent: matched by (parent, title, event_date), so re-running
updates existing rows instead of duplicating them. Silently skips any
customer_name/account_name that doesn't exist yet in the target
organisation.

Usage:
    python manage.py seed_demo_calendar_events --org-email alice@acme.io
"""

from django.core.management.base import BaseCommand, CommandError

from services.accounts.models import User
from services.customers.models import Account, CalendarEvent, Customer

# Org-level events — customer_name must match a Customer.name already
# seeded by seed_demo_customers.
DEMO_CUSTOMER_EVENTS = [
    {
        "customer_name": "Apple Inc",
        "title": "Quarterly Business Review",
        "description": "Q1 2026 QBR with stakeholders",
        "type": "review",
        "event_date": "2026-03-15",
        "start_time": "10:00",
        "end_time": "11:30",
        "attendee_count": 3,
    },
    {
        "customer_name": "Apple Inc",
        "title": "Product Demo: New Analytics Module",
        "description": "Showcase the new analytics capabilities",
        "type": "demo",
        "event_date": "2026-03-10",
        "start_time": "14:00",
        "end_time": "15:00",
        "attendee_count": 2,
    },
    {
        "customer_name": "Apple Inc",
        "title": "Weekly Sync — Account Health",
        "description": "Regular check-in on account metrics",
        "type": "call",
        "event_date": "2026-03-07",
        "start_time": "09:00",
        "end_time": "09:30",
        "attendee_count": 2,
    },
    {
        "customer_name": "Pizza Hut",
        "title": "Escalation Follow-Up",
        "description": "Review integration fix deployment",
        "type": "meeting",
        "event_date": "2026-03-05",
        "start_time": "11:00",
        "end_time": "11:30",
        "attendee_count": 2,
    },
    {
        "customer_name": "Kraft Heinz",
        "title": "Success Plan Kickoff",
        "description": "H1 2026 goals alignment",
        "type": "meeting",
        "event_date": "2026-03-03",
        "start_time": "15:00",
        "end_time": "16:00",
        "attendee_count": 2,
    },
    {
        "customer_name": "Arista Networks",
        "title": "Network Performance Review",
        "description": "Q2 benchmark results walkthrough",
        "type": "review",
        "event_date": "2026-06-11",
        "start_time": "10:00",
        "end_time": "11:00",
        "attendee_count": 3,
    },
    {
        "customer_name": "Hyatt Hotels Corporation",
        "title": "Booking API Incident Retro",
        "description": "Root cause review with engineering",
        "type": "meeting",
        "event_date": "2026-07-20",
        "start_time": "13:00",
        "end_time": "13:45",
        "attendee_count": 4,
    },
    {
        "customer_name": "Notion Labs",
        "title": "Onboarding Kickoff Call",
        "description": "Introduce the team and set up the workspace migration plan",
        "type": "call",
        "event_date": "2026-08-06",
        "start_time": "11:00",
        "end_time": "11:30",
        "attendee_count": 3,
    },
    {
        "customer_name": "Oracle",
        "title": "Executive Alignment Session",
        "description": "Align on Q3 roadmap priorities",
        "type": "meeting",
        "event_date": "2026-06-29",
        "start_time": "16:00",
        "end_time": "17:00",
        "attendee_count": 2,
    },
    {
        "customer_name": "Salesforce",
        "title": "H2 Success Plan Review",
        "description": "Walk through expansion metrics for H2",
        "type": "review",
        "event_date": "2026-07-02",
        "start_time": "10:30",
        "end_time": "11:30",
        "attendee_count": 3,
    },
    {
        "customer_name": "Shopify",
        "title": "Renewal Proposal Walkthrough",
        "description": "Present the renewal proposal to procurement",
        "type": "meeting",
        "event_date": "2026-08-13",
        "start_time": "09:00",
        "end_time": "09:45",
        "attendee_count": 3,
    },
    {
        "customer_name": "Spotify",
        "title": "Adoption Review Call",
        "description": "Review usage trends across the CS team",
        "type": "call",
        "event_date": "2026-07-09",
        "start_time": "11:15",
        "end_time": "11:45",
        "attendee_count": 2,
    },
    {
        "customer_name": "Stripe",
        "title": "Webhook Reliability Demo",
        "description": "Demo the new retry and monitoring improvements",
        "type": "demo",
        "event_date": "2026-06-15",
        "start_time": "09:00",
        "end_time": "10:00",
        "attendee_count": 3,
    },
    {
        "customer_name": "Twilio",
        "title": "Health Check Review",
        "description": "Review the July health check results",
        "type": "review",
        "event_date": "2026-07-26",
        "start_time": "13:00",
        "end_time": "13:30",
        "attendee_count": 2,
    },
    {
        "customer_name": "Uber",
        "title": "Executive Alignment Session",
        "description": "Align with leadership on renewal strategy",
        "type": "meeting",
        "event_date": "2026-08-19",
        "start_time": "15:00",
        "end_time": "16:00",
        "attendee_count": 4,
    },
    {
        "customer_name": "WeWork",
        "title": "Billing Discrepancy Call",
        "description": "Walk through the proration credit with finance",
        "type": "call",
        "event_date": "2026-07-03",
        "start_time": "08:30",
        "end_time": "09:00",
        "attendee_count": 3,
    },
    {
        "customer_name": "Zoom",
        "title": "Usage Review",
        "description": "Review August seat usage trends",
        "type": "review",
        "event_date": "2026-08-21",
        "start_time": "10:00",
        "end_time": "10:30",
        "attendee_count": 2,
    },
]

# Account-level events — customer_name/account_name must match an
# Account already seeded by seed_demo_accounts (under that customer).
DEMO_ACCOUNT_EVENTS = [
    {
        "customer_name": "Apple Inc",
        "account_name": "North America Enterprise",
        "title": "Q2 Business Review — Executive Session",
        "description": "Exec-level QBR with Tim Cook and Edgar Holmes",
        "type": "review",
        "event_date": "2026-04-05",
        "start_time": "10:00",
        "end_time": "11:30",
        "attendee_count": 3,
    },
    {
        "customer_name": "Apple Inc",
        "account_name": "North America Enterprise",
        "title": "Platform Demo: AI Analytics Launch",
        "description": "Demo of new AI-powered analytics dashboard for account stakeholders",
        "type": "demo",
        "event_date": "2026-03-28",
        "start_time": "14:00",
        "end_time": "15:00",
        "attendee_count": 2,
    },
    {
        "customer_name": "Apple Inc",
        "account_name": "Apple EMEA",
        "title": "Renewal Negotiation Call",
        "description": "Commercial terms alignment for 3-year renewal proposal",
        "type": "call",
        "event_date": "2026-04-10",
        "start_time": "11:00",
        "end_time": "12:00",
        "attendee_count": 2,
    },
    {
        "customer_name": "Apple Inc",
        "account_name": "Apple APAC",
        "title": "APAC Rollout Check-In",
        "description": "Status update on the remaining office rollouts",
        "type": "call",
        "event_date": "2026-06-20",
        "start_time": "09:00",
        "end_time": "09:30",
        "attendee_count": 2,
    },
    {
        "customer_name": "Kraft Heinz",
        "account_name": "APAC Division",
        "title": "Usage Review",
        "description": "Review the APAC division's usage uptick",
        "type": "review",
        "event_date": "2026-07-15",
        "start_time": "10:00",
        "end_time": "10:30",
        "attendee_count": 2,
    },
    {
        "customer_name": "Kraft Heinz",
        "account_name": "Heinz Europe",
        "title": "Renewal Timeline Meeting",
        "description": "Confirm renewal timeline with Europe leadership",
        "type": "meeting",
        "event_date": "2026-08-04",
        "start_time": "14:00",
        "end_time": "14:30",
        "attendee_count": 3,
    },
    {
        "customer_name": "Kraft Heinz",
        "account_name": "Kraft Heinz North America (Renamed)",
        "title": "Adoption Review Call",
        "description": "Discuss adoption wins with the North America team",
        "type": "call",
        "event_date": "2026-07-11",
        "start_time": "10:00",
        "end_time": "10:30",
        "attendee_count": 2,
    },
    {
        "customer_name": "Arista Networks",
        "account_name": "Arista Global",
        "title": "Global Rollout Sync",
        "description": "Sync on the global rollout blockers",
        "type": "meeting",
        "event_date": "2026-05-13",
        "start_time": "09:00",
        "end_time": "09:30",
        "attendee_count": 3,
    },
    {
        "customer_name": "Hyatt Hotels Corporation",
        "account_name": "Hyatt Americas",
        "title": "Renewal Proposal Review",
        "description": "Walk through the Americas renewal proposal",
        "type": "review",
        "event_date": "2026-07-20",
        "start_time": "11:00",
        "end_time": "11:45",
        "attendee_count": 3,
    },
    {
        "customer_name": "Hyatt Hotels Corporation",
        "account_name": "Hyatt EMEA & APAC",
        "title": "Onboarding Milestone Check-In",
        "description": "Confirm the EMEA & APAC onboarding milestone",
        "type": "call",
        "event_date": "2026-06-24",
        "start_time": "13:00",
        "end_time": "13:30",
        "attendee_count": 2,
    },
    {
        "customer_name": "Oracle",
        "account_name": "Oracle Cloud Division",
        "title": "Cloud Migration Status Meeting",
        "description": "Status update on the stalled cloud migration",
        "type": "meeting",
        "event_date": "2026-08-14",
        "start_time": "10:00",
        "end_time": "10:45",
        "attendee_count": 4,
    },
    {
        "customer_name": "Pizza Hut",
        "account_name": "Pizza Hut International",
        "title": "International Leadership Sync",
        "description": "Align on Q3 goals with international leadership",
        "type": "meeting",
        "event_date": "2026-05-27",
        "start_time": "09:00",
        "end_time": "09:30",
        "attendee_count": 3,
    },
    {
        "customer_name": "Pizza Hut",
        "account_name": "Pizza Hut US Operations",
        "title": "Onboarding Milestone Demo",
        "description": "Demo the milestone tracker fix",
        "type": "demo",
        "event_date": "2026-08-22",
        "start_time": "14:00",
        "end_time": "14:30",
        "attendee_count": 2,
    },
    {
        "customer_name": "Salesforce",
        "account_name": "Salesforce Core Platform",
        "title": "Core Platform Health Review",
        "description": "Review the Core Platform health check results",
        "type": "review",
        "event_date": "2026-07-30",
        "start_time": "11:00",
        "end_time": "11:30",
        "attendee_count": 2,
    },
    {
        "customer_name": "Shopify",
        "account_name": "Shopify Plus",
        "title": "Checkout Reliability Call",
        "description": "Discuss the checkout error rate limit fix",
        "type": "call",
        "event_date": "2026-06-28",
        "start_time": "08:30",
        "end_time": "09:00",
        "attendee_count": 3,
    },
    {
        "customer_name": "Spotify",
        "account_name": "Spotify Business",
        "title": "Business Tier Onboarding Check-In",
        "description": "Confirm the business tier team is fully ramped",
        "type": "call",
        "event_date": "2026-05-01",
        "start_time": "10:00",
        "end_time": "10:30",
        "attendee_count": 2,
    },
    {
        "customer_name": "Stripe",
        "account_name": "Stripe Payments",
        "title": "Payments Adoption Review",
        "description": "Review adoption wins for the Payments account",
        "type": "review",
        "event_date": "2026-06-02",
        "start_time": "09:00",
        "end_time": "09:30",
        "attendee_count": 2,
    },
    {
        "customer_name": "WeWork",
        "account_name": "WeWork US",
        "title": "US Renewal Path Meeting",
        "description": "Align with WeWork US leadership on renewal path",
        "type": "meeting",
        "event_date": "2026-07-24",
        "start_time": "14:00",
        "end_time": "14:45",
        "attendee_count": 3,
    },
]


class Command(BaseCommand):
    help = "Seeds demo CalendarEvent rows under existing demo Customers/Accounts."

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

        for row in DEMO_CUSTOMER_EVENTS:
            try:
                customer = Customer.objects.get(organisation=org, name=row["customer_name"])
            except Customer.DoesNotExist:
                self.stderr.write(
                    f"  skipping event — no customer {row['customer_name']!r} in {org.name}."
                )
                skipped += 1
                continue

            _, was_created = CalendarEvent.objects.update_or_create(
                customer=customer,
                title=row["title"],
                event_date=row["event_date"],
                defaults={
                    "description": row["description"],
                    "type": row["type"],
                    "start_time": row["start_time"],
                    "end_time": row["end_time"],
                    "attendee_count": row["attendee_count"],
                },
            )
            created += was_created
            updated += not was_created

        for row in DEMO_ACCOUNT_EVENTS:
            try:
                account = Account.objects.filter(
                    customers__organisation=org,
                    customers__name=row["customer_name"],
                    name=row["account_name"],
                ).distinct().get()
            except Account.DoesNotExist:
                self.stderr.write(
                    f"  skipping event — no account {row['account_name']!r} under "
                    f"{row['customer_name']!r} in {org.name}."
                )
                skipped += 1
                continue

            _, was_created = CalendarEvent.objects.update_or_create(
                account=account,
                title=row["title"],
                event_date=row["event_date"],
                defaults={
                    "description": row["description"],
                    "type": row["type"],
                    "start_time": row["start_time"],
                    "end_time": row["end_time"],
                    "attendee_count": row["attendee_count"],
                },
            )
            created += was_created
            updated += not was_created

        self.stdout.write(
            self.style.SUCCESS(
                f"{org.name}: created {created}, updated {updated}, "
                f"skipped {skipped} calendar event(s)."
            )
        )
