"""Dev/demo convenience — not part of the product. Seeds Note rows
under existing demo Customers and Accounts so ActivityFeed's "Notes"
filter has real, per-entity data on both the Organization Details
page (General tab) and the standalone Account page — run after
seed_demo_customers and seed_demo_accounts.

Covers every company seed_demo_customers creates (1-3 org-level notes
each) and every sub-account seed_demo_accounts creates (1-2
account-level notes each) — a handful match the original
activityData.ts/accountActivityData.ts mock content for the companies
that mock happened to name; the rest are new demo content, same
reasoning as seed_demo_activities/seed_demo_emails/seed_demo_tasks.

`links` varies across 0 and a few positive counts on purpose — the
card only renders its link line when links > 0 ("links if any"), so
this exercises both branches when clicking through the seeded data.

Idempotent: matched by (parent, title, logged_at), so re-running
updates existing rows instead of duplicating them. Silently skips any
customer_name/account_name that doesn't exist yet in the target
organisation.

Usage:
    python manage.py seed_demo_notes --org-email alice@acme.io
"""

from django.core.management.base import BaseCommand, CommandError

from services.accounts.models import User
from services.customers.models import Account, Customer, Note

# Org-level notes — customer_name must match a Customer.name already
# seeded by seed_demo_customers.
DEMO_CUSTOMER_NOTES = [
    {
        "customer_name": "Apple Inc",
        "title": "Call Notes: Product Feedback Session",
        "author_name": "Edgar Holmes",
        "body": "Customer expressed interest in AI-powered analytics. Wants "
        "better integration with existing BI tools. Follow up with product "
        "team on roadmap.",
        "logged_at": "2026-03-04",
        "links": 2,
    },
    {
        "customer_name": "Apple Inc",
        "title": "Renewal Strategy Discussion",
        "author_name": "Natalie Reyes",
        "body": "Multi-year deal preferred. Customer open to expansion if we "
        "can deliver the analytics dashboard by Q2. CFO approval needed.",
        "logged_at": "2026-02-28",
        "links": 0,
    },
    {
        "customer_name": "Apple Inc",
        "title": "Technical Requirements Gathering",
        "author_name": "Sarah Chen",
        "body": "SSO integration required before go-live. API rate limits "
        "need to be discussed. Security review pending from their IT team.",
        "logged_at": "2026-02-25",
        "links": 1,
    },
    {
        "customer_name": "Pizza Hut",
        "title": "Escalation Meeting Summary",
        "author_name": "Edgar Holmes",
        "body": "Integration failures root cause identified — API version "
        "mismatch. Fix deployed, monitoring for 48 hours.",
        "logged_at": "2026-03-03",
        "links": 3,
    },
    {
        "customer_name": "Kraft Heinz",
        "title": "H1 Planning Review",
        "author_name": "Edgar Holmes",
        "body": "Agreed on 3 key metrics for H1. Customer wants monthly "
        "check-ins. Next review scheduled for April 1st.",
        "logged_at": "2026-03-01",
        "links": 0,
    },
    {
        "customer_name": "Arista Networks",
        "title": "Network Health Review Notes",
        "author_name": "Natalie Reyes",
        "body": "Latency benchmarks look strong post-upgrade. No open incidents this cycle.",
        "logged_at": "2026-06-12",
        "links": 1,
    },
    {
        "customer_name": "Hyatt Hotels Corporation",
        "title": "Booking API Incident Retro",
        "author_name": "Edgar Holmes",
        "body": "Root cause was a stale cache entry during a deploy. Added "
        "an alert to catch this earlier next time.",
        "logged_at": "2026-07-20",
        "links": 2,
    },
    {
        "customer_name": "Notion Labs",
        "title": "Kickoff Call Notes",
        "author_name": "Edgar Holmes",
        "body": "Team is excited to get started. Main priority is workspace "
        "migration from their old tool.",
        "logged_at": "2026-08-06",
        "links": 0,
    },
    {
        "customer_name": "Oracle",
        "title": "Executive Sponsor Check-In",
        "author_name": "Sarah Chen",
        "body": "VP of Ops reaffirmed commitment to the Q3 roadmap. Asked "
        "for a mid-quarter progress update.",
        "logged_at": "2026-06-29",
        "links": 1,
    },
    {
        "customer_name": "Salesforce",
        "title": "H2 Success Plan Notes",
        "author_name": "Edgar Holmes",
        "body": "Three expansion metrics agreed. Customer wants a shared "
        "dashboard to track progress together.",
        "logged_at": "2026-07-02",
        "links": 0,
    },
    {
        "customer_name": "Shopify",
        "title": "Procurement Call Notes",
        "author_name": "Natalie Reyes",
        "body": "Procurement confirmed budget approval is on track for this "
        "quarter's renewal cycle.",
        "logged_at": "2026-08-13",
        "links": 2,
    },
    {
        "customer_name": "Spotify",
        "title": "Adoption Review Notes",
        "author_name": "Edgar Holmes",
        "body": "Usage across the CS team is strong. Suggested a lunch-and-"
        "learn for the newer hires.",
        "logged_at": "2026-07-09",
        "links": 0,
    },
    {
        "customer_name": "Stripe",
        "title": "Webhook Escalation Notes",
        "author_name": "Edgar Holmes",
        "body": "Delivery delays traced to a downstream queue backlog. "
        "Engineering added extra capacity.",
        "logged_at": "2026-06-15",
        "links": 3,
    },
    {
        "customer_name": "Twilio",
        "title": "Health Check Notes",
        "author_name": "Sarah Chen",
        "body": "Score holding steady at 9.1. No churn signals present this cycle.",
        "logged_at": "2026-07-26",
        "links": 0,
    },
    {
        "customer_name": "Uber",
        "title": "Executive Alignment Notes",
        "author_name": "Edgar Holmes",
        "body": "Leadership aligned on renewal timeline. Wants a joint "
        "roadmap review next quarter.",
        "logged_at": "2026-08-19",
        "links": 1,
    },
    {
        "customer_name": "WeWork",
        "title": "Billing Discrepancy Notes",
        "author_name": "Edgar Holmes",
        "body": "Discrepancy traced to a proration error on a mid-cycle seat "
        "change. Finance is issuing a credit.",
        "logged_at": "2026-07-03",
        "links": 2,
    },
    {
        "customer_name": "Zoom",
        "title": "Usage Review Notes",
        "author_name": "Sarah Chen",
        "body": "Licensed seat usage remains strong. No red flags to report this cycle.",
        "logged_at": "2026-08-21",
        "links": 0,
    },
]

# Account-level notes — customer_name/account_name must match an
# Account already seeded by seed_demo_accounts (under that customer).
DEMO_ACCOUNT_NOTES = [
    {
        "customer_name": "Apple Inc",
        "account_name": "North America Enterprise",
        "title": "Executive Sponsor Meeting Notes",
        "author_name": "Edgar Holmes",
        "body": "Tim expressed high satisfaction with the platform. Wants AI "
        "dashboards to be GA by end of Q2. No blockers currently.",
        "logged_at": "2026-03-20",
        "links": 2,
    },
    {
        "customer_name": "Apple Inc",
        "account_name": "North America Enterprise",
        "title": "Technical Handoff Notes",
        "author_name": "Natalie Reyes",
        "body": "SSO is fully configured. API keys rotated. Security scan "
        "passed. IT team signed off on enterprise compliance requirements.",
        "logged_at": "2026-03-10",
        "links": 0,
    },
    {
        "customer_name": "Apple Inc",
        "account_name": "Apple EMEA",
        "title": "Commercial Negotiation Summary",
        "author_name": "Edgar Holmes",
        "body": "Customer requested 15% discount for 3-year commitment. "
        "Legal reviewing terms. Decision expected by Apr 10th.",
        "logged_at": "2026-03-15",
        "links": 1,
    },
    {
        "customer_name": "Apple Inc",
        "account_name": "Apple APAC",
        "title": "APAC Rollout Notes",
        "author_name": "Natalie Reyes",
        "body": "Three offices fully onboarded. Fourth office scheduled for next month.",
        "logged_at": "2026-06-20",
        "links": 0,
    },
    {
        "customer_name": "Kraft Heinz",
        "account_name": "APAC Division",
        "title": "Usage Uptick Notes",
        "author_name": "Sarah Chen",
        "body": "Adoption ticked up noticeably after the new regional rollout completed.",
        "logged_at": "2026-07-15",
        "links": 1,
    },
    {
        "customer_name": "Kraft Heinz",
        "account_name": "Heinz Europe",
        "title": "Renewal Timeline Notes",
        "author_name": "Edgar Holmes",
        "body": "Europe leadership confirmed target renewal date. No outstanding blockers.",
        "logged_at": "2026-08-04",
        "links": 0,
    },
    {
        "customer_name": "Kraft Heinz",
        "account_name": "Kraft Heinz North America (Renamed)",
        "title": "Adoption Summary Notes",
        "author_name": "Edgar Holmes",
        "body": "North America account showing consistent week-over-week engagement growth.",
        "logged_at": "2026-07-11",
        "links": 2,
    },
    {
        "customer_name": "Arista Networks",
        "account_name": "Arista Global",
        "title": "Data Sync Incident Notes",
        "author_name": "Natalie Reyes",
        "body": "Sync delay resolved — root cause was a misconfigured retry interval.",
        "logged_at": "2026-05-13",
        "links": 1,
    },
    {
        "customer_name": "Hyatt Hotels Corporation",
        "account_name": "Hyatt Americas",
        "title": "Renewal Proposal Notes",
        "author_name": "Edgar Holmes",
        "body": "Proposal sent to procurement. Awaiting sign-off before end of quarter.",
        "logged_at": "2026-07-20",
        "links": 0,
    },
    {
        "customer_name": "Hyatt Hotels Corporation",
        "account_name": "Hyatt EMEA & APAC",
        "title": "Onboarding Milestone Notes",
        "author_name": "Natalie Reyes",
        "body": "Region hit its first onboarding milestone ahead of schedule.",
        "logged_at": "2026-06-24",
        "links": 0,
    },
    {
        "customer_name": "Oracle",
        "account_name": "Oracle Cloud Division",
        "title": "Cloud Migration Delay Notes",
        "author_name": "Edgar Holmes",
        "body": "Migration delay escalated to engineering — new ETA is next sprint.",
        "logged_at": "2026-08-14",
        "links": 3,
    },
    {
        "customer_name": "Pizza Hut",
        "account_name": "Pizza Hut International",
        "title": "International Leadership Notes",
        "author_name": "Edgar Holmes",
        "body": "Aligned on Q3 goals with international leadership team.",
        "logged_at": "2026-05-27",
        "links": 0,
    },
    {
        "customer_name": "Pizza Hut",
        "account_name": "Pizza Hut US Operations",
        "title": "Onboarding Milestone Notes",
        "author_name": "Natalie Reyes",
        "body": "US Operations completed their second onboarding milestone on schedule.",
        "logged_at": "2026-08-22",
        "links": 1,
    },
    {
        "customer_name": "Salesforce",
        "account_name": "Salesforce Core Platform",
        "title": "Health Check Notes",
        "author_name": "Sarah Chen",
        "body": "Core Platform health check came back clean across all metrics.",
        "logged_at": "2026-07-30",
        "links": 0,
    },
    {
        "customer_name": "Shopify",
        "account_name": "Shopify Plus",
        "title": "Checkout Errors Notes",
        "author_name": "Sarah Chen",
        "body": "Checkout errors traced to a peak-load rate limit — limit "
        "raised for the Plus tier.",
        "logged_at": "2026-06-27",
        "links": 2,
    },
    {
        "customer_name": "Spotify",
        "account_name": "Spotify Business",
        "title": "Business Tier Onboarding Notes",
        "author_name": "Natalie Reyes",
        "body": "Business tier team is fully ramped on core workflows.",
        "logged_at": "2026-05-01",
        "links": 0,
    },
    {
        "customer_name": "Stripe",
        "account_name": "Stripe Payments",
        "title": "Adoption Wins Notes",
        "author_name": "Natalie Reyes",
        "body": "Payments account team highlighted several workflow wins this quarter.",
        "logged_at": "2026-06-02",
        "links": 1,
    },
    {
        "customer_name": "WeWork",
        "account_name": "WeWork US",
        "title": "Renewal Path Notes",
        "author_name": "Edgar Holmes",
        "body": "US leadership aligned on renewal path — no blockers identified.",
        "logged_at": "2026-07-24",
        "links": 0,
    },
]


class Command(BaseCommand):
    help = "Seeds demo Note rows under existing demo Customers/Accounts."

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

        for row in DEMO_CUSTOMER_NOTES:
            try:
                customer = Customer.objects.get(organisation=org, name=row["customer_name"])
            except Customer.DoesNotExist:
                self.stderr.write(
                    f"  skipping note — no customer {row['customer_name']!r} in {org.name}."
                )
                skipped += 1
                continue

            _, was_created = Note.objects.update_or_create(
                customer=customer,
                title=row["title"],
                logged_at=row["logged_at"],
                defaults={
                    "author_name": row["author_name"],
                    "body": row["body"],
                    "links": row["links"],
                },
            )
            created += was_created
            updated += not was_created

        for row in DEMO_ACCOUNT_NOTES:
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
                    f"  skipping note — no account {row['account_name']!r} under "
                    f"{row['customer_name']!r} in {org.name}."
                )
                skipped += 1
                continue

            _, was_created = Note.objects.update_or_create(
                account=account,
                title=row["title"],
                logged_at=row["logged_at"],
                defaults={
                    "author_name": row["author_name"],
                    "body": row["body"],
                    "links": row["links"],
                },
            )
            created += was_created
            updated += not was_created

        self.stdout.write(
            self.style.SUCCESS(
                f"{org.name}: created {created}, updated {updated}, skipped {skipped} note(s)."
            )
        )
