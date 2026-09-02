"""Dev/demo convenience — not part of the product. Seeds Ticket rows
under existing demo Customers and Accounts so ActivityFeed's "Tickets"
filter has real, per-entity data on both the Organization Details
page (General tab) and the standalone Account page — run after
seed_demo_customers and seed_demo_accounts.

Covers every company seed_demo_customers creates (1-3 org-level
tickets each) and every sub-account seed_demo_accounts creates (1-2
account-level tickets each) — a handful match the original
activityData.ts/accountActivityData.ts mock content for the companies
that mock happened to name; the rest are new demo content, same
reasoning as seed_demo_activities/seed_demo_emails/seed_demo_tasks/
seed_demo_notes.

`links` varies across 0 and a few positive counts on purpose — the
card only renders its link line when links > 0, same as Note.
`priority` also varies across all four levels on purpose — unlike the
old mock, this pass actually colors the card's flag icon by priority.

Idempotent: matched by (parent, ticket_number), so re-running updates
existing rows instead of duplicating them. Silently skips any
customer_name/account_name that doesn't exist yet in the target
organisation.

Usage:
    python manage.py seed_demo_tickets --org-email alice@acme.io
"""

from django.core.management.base import BaseCommand, CommandError

from services.accounts.models import User
from services.customers.models import Account, Customer, Ticket

# Org-level tickets — customer_name must match a Customer.name already
# seeded by seed_demo_customers.
DEMO_CUSTOMER_TICKETS = [
    {
        "customer_name": "Apple Inc",
        "ticket_number": "TKT-1042",
        "title": "Dashboard loading slow on large datasets",
        "assignee_name": "Support Team",
        "status": "in-progress",
        "priority": "high",
        "opened_at": "2026-03-03",
        "links": 2,
    },
    {
        "customer_name": "Apple Inc",
        "ticket_number": "TKT-1038",
        "title": "Export to CSV not including all columns",
        "assignee_name": "Support Team",
        "status": "resolved",
        "priority": "medium",
        "opened_at": "2026-02-27",
        "links": 0,
    },
    {
        "customer_name": "Apple Inc",
        "ticket_number": "TKT-1035",
        "title": "SSO login redirect loop",
        "assignee_name": "Engineering",
        "status": "closed",
        "priority": "critical",
        "opened_at": "2026-02-20",
        "links": 1,
    },
    {
        "customer_name": "Pizza Hut",
        "ticket_number": "TKT-1045",
        "title": "Salesforce sync failure",
        "assignee_name": "Engineering",
        "status": "open",
        "priority": "critical",
        "opened_at": "2026-03-02",
        "links": 3,
    },
    {
        "customer_name": "Kraft Heinz",
        "ticket_number": "TKT-1041",
        "title": "Custom report template not saving",
        "assignee_name": "Support Team",
        "status": "in-progress",
        "priority": "medium",
        "opened_at": "2026-03-01",
        "links": 0,
    },
    {
        "customer_name": "Arista Networks",
        "ticket_number": "TKT-3001",
        "title": "Firmware update fails on legacy switches",
        "assignee_name": "Engineering",
        "status": "open",
        "priority": "high",
        "opened_at": "2026-06-11",
        "links": 1,
    },
    {
        "customer_name": "Hyatt Hotels Corporation",
        "ticket_number": "TKT-3002",
        "title": "Booking confirmation emails delayed",
        "assignee_name": "Support Team",
        "status": "in-progress",
        "priority": "critical",
        "opened_at": "2026-07-19",
        "links": 2,
    },
    {
        "customer_name": "Notion Labs",
        "ticket_number": "TKT-3003",
        "title": "Workspace import stuck at 90%",
        "assignee_name": "Engineering",
        "status": "open",
        "priority": "medium",
        "opened_at": "2026-08-06",
        "links": 0,
    },
    {
        "customer_name": "Oracle",
        "ticket_number": "TKT-3004",
        "title": "Cloud console permissions not syncing",
        "assignee_name": "Support Team",
        "status": "resolved",
        "priority": "low",
        "opened_at": "2026-06-29",
        "links": 0,
    },
    {
        "customer_name": "Salesforce",
        "ticket_number": "TKT-3005",
        "title": "Report builder times out on large datasets",
        "assignee_name": "Engineering",
        "status": "in-progress",
        "priority": "high",
        "opened_at": "2026-07-02",
        "links": 1,
    },
    {
        "customer_name": "Shopify",
        "ticket_number": "TKT-3006",
        "title": "Checkout webhook occasionally duplicated",
        "assignee_name": "Engineering",
        "status": "open",
        "priority": "critical",
        "opened_at": "2026-08-13",
        "links": 2,
    },
    {
        "customer_name": "Spotify",
        "ticket_number": "TKT-3007",
        "title": "Playlist sync lag for shared accounts",
        "assignee_name": "Support Team",
        "status": "resolved",
        "priority": "low",
        "opened_at": "2026-07-09",
        "links": 0,
    },
    {
        "customer_name": "Stripe",
        "ticket_number": "TKT-3008",
        "title": "Webhook retries exceeding rate limit",
        "assignee_name": "Engineering",
        "status": "in-progress",
        "priority": "high",
        "opened_at": "2026-06-15",
        "links": 3,
    },
    {
        "customer_name": "Twilio",
        "ticket_number": "TKT-3009",
        "title": "SMS delivery reports missing timestamps",
        "assignee_name": "Support Team",
        "status": "open",
        "priority": "medium",
        "opened_at": "2026-07-26",
        "links": 0,
    },
    {
        "customer_name": "Uber",
        "ticket_number": "TKT-3010",
        "title": "Dashboard filters reset unexpectedly",
        "assignee_name": "Engineering",
        "status": "closed",
        "priority": "low",
        "opened_at": "2026-08-19",
        "links": 1,
    },
    {
        "customer_name": "WeWork",
        "ticket_number": "TKT-3011",
        "title": "Billing export missing line items",
        "assignee_name": "Support Team",
        "status": "in-progress",
        "priority": "critical",
        "opened_at": "2026-07-03",
        "links": 2,
    },
    {
        "customer_name": "Zoom",
        "ticket_number": "TKT-3012",
        "title": "Recording playback buffering on mobile",
        "assignee_name": "Engineering",
        "status": "open",
        "priority": "medium",
        "opened_at": "2026-08-21",
        "links": 0,
    },
]

# Account-level tickets — customer_name/account_name must match an
# Account already seeded by seed_demo_accounts (under that customer).
DEMO_ACCOUNT_TICKETS = [
    {
        "customer_name": "Apple Inc",
        "account_name": "North America Enterprise",
        "ticket_number": "TKT-2001",
        "title": "API rate limit exceeded during batch import",
        "assignee_name": "Engineering",
        "status": "in-progress",
        "priority": "high",
        "opened_at": "2026-03-26",
        "links": 1,
    },
    {
        "customer_name": "Apple Inc",
        "account_name": "North America Enterprise",
        "ticket_number": "TKT-2002",
        "title": "Custom field display bug in analytics view",
        "assignee_name": "Support Team",
        "status": "resolved",
        "priority": "medium",
        "opened_at": "2026-03-18",
        "links": 0,
    },
    {
        "customer_name": "Apple Inc",
        "account_name": "Apple EMEA",
        "ticket_number": "TKT-2003",
        "title": "Mobile app login fails on iOS 18",
        "assignee_name": "Engineering",
        "status": "open",
        "priority": "critical",
        "opened_at": "2026-03-29",
        "links": 2,
    },
    {
        "customer_name": "Apple Inc",
        "account_name": "Apple APAC",
        "ticket_number": "TKT-2004",
        "title": "Regional pricing display incorrect for APAC",
        "assignee_name": "Support Team",
        "status": "open",
        "priority": "medium",
        "opened_at": "2026-06-20",
        "links": 0,
    },
    {
        "customer_name": "Kraft Heinz",
        "account_name": "APAC Division",
        "ticket_number": "TKT-2005",
        "title": "Usage dashboard not loading for APAC users",
        "assignee_name": "Engineering",
        "status": "in-progress",
        "priority": "high",
        "opened_at": "2026-07-15",
        "links": 1,
    },
    {
        "customer_name": "Kraft Heinz",
        "account_name": "Heinz Europe",
        "ticket_number": "TKT-2006",
        "title": "SSO certificate expiring soon",
        "assignee_name": "Engineering",
        "status": "open",
        "priority": "critical",
        "opened_at": "2026-08-04",
        "links": 0,
    },
    {
        "customer_name": "Kraft Heinz",
        "account_name": "Kraft Heinz North America (Renamed)",
        "ticket_number": "TKT-2007",
        "title": "Duplicate contacts appearing after import",
        "assignee_name": "Support Team",
        "status": "resolved",
        "priority": "low",
        "opened_at": "2026-07-11",
        "links": 0,
    },
    {
        "customer_name": "Arista Networks",
        "account_name": "Arista Global",
        "ticket_number": "TKT-2008",
        "title": "Global rollout blocked by data sync delay",
        "assignee_name": "Engineering",
        "status": "in-progress",
        "priority": "high",
        "opened_at": "2026-05-13",
        "links": 2,
    },
    {
        "customer_name": "Hyatt Hotels Corporation",
        "account_name": "Hyatt Americas",
        "ticket_number": "TKT-2009",
        "title": "Renewal proposal PDF fails to generate",
        "assignee_name": "Support Team",
        "status": "open",
        "priority": "medium",
        "opened_at": "2026-07-20",
        "links": 0,
    },
    {
        "customer_name": "Hyatt Hotels Corporation",
        "account_name": "Hyatt EMEA & APAC",
        "ticket_number": "TKT-2010",
        "title": "Onboarding checklist stuck on step 3",
        "assignee_name": "Engineering",
        "status": "resolved",
        "priority": "low",
        "opened_at": "2026-06-24",
        "links": 0,
    },
    {
        "customer_name": "Oracle",
        "account_name": "Oracle Cloud Division",
        "ticket_number": "TKT-2011",
        "title": "Cloud migration stalled on data validation",
        "assignee_name": "Engineering",
        "status": "in-progress",
        "priority": "critical",
        "opened_at": "2026-08-14",
        "links": 3,
    },
    {
        "customer_name": "Pizza Hut",
        "account_name": "Pizza Hut International",
        "ticket_number": "TKT-2012",
        "title": "Currency conversion rounding error",
        "assignee_name": "Support Team",
        "status": "open",
        "priority": "medium",
        "opened_at": "2026-05-27",
        "links": 0,
    },
    {
        "customer_name": "Pizza Hut",
        "account_name": "Pizza Hut US Operations",
        "ticket_number": "TKT-2013",
        "title": "Onboarding milestone tracker not updating",
        "assignee_name": "Engineering",
        "status": "resolved",
        "priority": "low",
        "opened_at": "2026-08-22",
        "links": 1,
    },
    {
        "customer_name": "Salesforce",
        "account_name": "Salesforce Core Platform",
        "ticket_number": "TKT-2014",
        "title": "Core Platform health check dashboard blank",
        "assignee_name": "Support Team",
        "status": "in-progress",
        "priority": "high",
        "opened_at": "2026-07-30",
        "links": 0,
    },
    {
        "customer_name": "Shopify",
        "account_name": "Shopify Plus",
        "ticket_number": "TKT-2015",
        "title": "Checkout errors during peak load",
        "assignee_name": "Engineering",
        "status": "open",
        "priority": "critical",
        "opened_at": "2026-06-28",
        "links": 2,
    },
    {
        "customer_name": "Spotify",
        "account_name": "Spotify Business",
        "ticket_number": "TKT-2016",
        "title": "Business tier seat count mismatch",
        "assignee_name": "Support Team",
        "status": "resolved",
        "priority": "low",
        "opened_at": "2026-05-01",
        "links": 0,
    },
    {
        "customer_name": "Stripe",
        "account_name": "Stripe Payments",
        "ticket_number": "TKT-2017",
        "title": "Payments account webhook signature mismatch",
        "assignee_name": "Engineering",
        "status": "in-progress",
        "priority": "critical",
        "opened_at": "2026-06-02",
        "links": 1,
    },
    {
        "customer_name": "WeWork",
        "account_name": "WeWork US",
        "ticket_number": "TKT-2018",
        "title": "US account billing credit not applied",
        "assignee_name": "Support Team",
        "status": "open",
        "priority": "medium",
        "opened_at": "2026-07-24",
        "links": 0,
    },
]


class Command(BaseCommand):
    help = "Seeds demo Ticket rows under existing demo Customers/Accounts."

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

        for row in DEMO_CUSTOMER_TICKETS:
            try:
                customer = Customer.objects.get(organisation=org, name=row["customer_name"])
            except Customer.DoesNotExist:
                self.stderr.write(
                    f"  skipping ticket — no customer {row['customer_name']!r} in {org.name}."
                )
                skipped += 1
                continue

            _, was_created = Ticket.objects.update_or_create(
                customer=customer,
                ticket_number=row["ticket_number"],
                defaults={
                    "title": row["title"],
                    "assignee_name": row["assignee_name"],
                    "status": row["status"],
                    "priority": row["priority"],
                    "opened_at": row["opened_at"],
                    "links": row["links"],
                },
            )
            created += was_created
            updated += not was_created

        for row in DEMO_ACCOUNT_TICKETS:
            try:
                account = Account.objects.get(
                    customer__organisation=org,
                    customer__name=row["customer_name"],
                    name=row["account_name"],
                )
            except Account.DoesNotExist:
                self.stderr.write(
                    f"  skipping ticket — no account {row['account_name']!r} under "
                    f"{row['customer_name']!r} in {org.name}."
                )
                skipped += 1
                continue

            _, was_created = Ticket.objects.update_or_create(
                account=account,
                ticket_number=row["ticket_number"],
                defaults={
                    "title": row["title"],
                    "assignee_name": row["assignee_name"],
                    "status": row["status"],
                    "priority": row["priority"],
                    "opened_at": row["opened_at"],
                    "links": row["links"],
                },
            )
            created += was_created
            updated += not was_created

        self.stdout.write(
            self.style.SUCCESS(
                f"{org.name}: created {created}, updated {updated}, "
                f"skipped {skipped} ticket(s)."
            )
        )
