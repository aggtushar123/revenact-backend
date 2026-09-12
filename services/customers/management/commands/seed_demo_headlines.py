"""Dev/demo convenience — not part of the product. Seeds Headline rows
under existing demo Customers and Accounts so the "Headlines" sub-tab
of ActivityFeed has real, per-entity data on both the Organization
Details page and the standalone Account page — run after
seed_demo_customers and seed_demo_accounts.

Deliberately hand-written rather than generated: `manage.py
seed_demo_*` must work offline with no ANTHROPIC_API_KEY set, same as
every other seed command here. To see the real generator instead, POST
to `/api/v1/customers/<id>/accounts/<id>/headlines/generate/` — that
path is what the "Data sources" footer describes, and these rows carry
`generated_at=None` precisely so a later regenerate treats them as
hand-written and leaves them alone.

The Apple EMEA rows reproduce the two cards the frontend's own
HEADLINES_DATA array used to hardcode, so the tab looks the same after
the swap as before it — the difference being that they now belong to
Apple EMEA alone, instead of appearing under every account in the
system. The rest is new demo content, same reasoning as
seed_demo_notes/seed_demo_emails.

Idempotent: matched by (parent, title), so re-running updates existing
rows instead of duplicating them. Silently skips any customer_name/
account_name that doesn't exist yet in the target organisation.

Usage:
    python manage.py seed_demo_headlines --org-email alice@acme.io
"""

from django.core.management.base import BaseCommand, CommandError

from services.accounts.models import User
from services.customers.models import Account, Customer, Headline

ALL_SOURCES = ["notes", "emails", "call_transcripts", "tickets"]

# Org-level headlines — customer_name must match a Customer.name already
# seeded by seed_demo_customers.
DEMO_CUSTOMER_HEADLINES = [
    {
        "customer_name": "Apple Inc",
        "kind": Headline.Kind.SUMMARY,
        "title": "TL;DR (Last 3 months)",
        "content": "Apple Inc is in a strong position across all three regional "
        "accounts, with EMEA leading on utilization and APAC still mid-rollout. "
        "Renewal conversations opened early and have stayed constructive; the "
        "main open thread is a 15% discount request tied to a three-year "
        "commitment, now with legal. SSO and the security review are closed out. "
        "No escalations this period.",
        "time_period_label": "Last 3 months",
        "data_sources": ALL_SOURCES,
    },
    {
        "customer_name": "Apple Inc",
        "title": "Three-Year Commitment Under Legal Review",
        "content": "Apple asked for a 15% discount in exchange for a three-year "
        "commitment, raised by Edgar Holmes in the March commercial negotiation "
        "and still with legal. Procurement signalled a decision by April 10th. "
        "The technical handoff is already complete — SSO configured, API keys "
        "rotated, security scan passed — so nothing technical is gating this.",
        "status": Headline.Status.IN_PROGRESS,
        "period_start": "2026-02-25",
        "period_end": "2026-03-20",
        "data_sources": ["notes", "emails"],
    },
    {
        "customer_name": "Pizza Hut",
        "kind": Headline.Kind.SUMMARY,
        "title": "TL;DR (Last 3 months)",
        "content": "Pizza Hut spent the period recovering from an integration "
        "failure rather than expanding. Root cause was an API version mismatch, "
        "identified and fixed with 48 hours of monitoring after. Q3 goals are "
        "aligned with the international leadership team, and US Operations "
        "cleared its second onboarding milestone on schedule. Sentiment "
        "recovered but the account is not yet back to growth conversations.",
        "time_period_label": "Last 3 months",
        "data_sources": ["notes", "tickets"],
    },
    {
        "customer_name": "Pizza Hut",
        "title": "Integration Failure Escalation and Recovery",
        "content": "Integration failures escalated to a full incident review "
        "chaired by Edgar Holmes. Root cause was an API version mismatch on our "
        "side; the fix shipped and held through 48 hours of monitoring. The "
        "account has since returned to normal cadence, with Q3 goals agreed "
        "with international leadership.",
        "status": Headline.Status.CLOSED,
        "period_start": "2026-03-03",
        "period_end": "2026-05-27",
        "data_sources": ["notes", "tickets"],
    },
    {
        "customer_name": "Oracle",
        "title": "Cloud Migration Slipping Against Roadmap",
        "content": "The Oracle Cloud Division migration missed its original "
        "window and was escalated to engineering, with a revised ETA of next "
        "sprint. The executive sponsor reaffirmed commitment to the Q3 roadmap "
        "in a separate check-in with Sarah Chen and asked for a mid-quarter "
        "progress update, so the relationship is intact but the delivery date "
        "is the live risk.",
        "status": Headline.Status.OPEN,
        "period_start": "2026-06-29",
        "period_end": "2026-08-14",
        "data_sources": ["notes", "emails", "tickets"],
    },
    {
        "customer_name": "Stripe",
        "title": "Webhook Delivery Delays Traced Downstream",
        "content": "Stripe reported webhook delivery delays, traced to a "
        "downstream queue backlog rather than anything in the integration "
        "itself. Engineering added capacity and the backlog cleared. The "
        "Payments account team separately highlighted several workflow wins "
        "this quarter, so the incident did not dent adoption.",
        "status": Headline.Status.CLOSED,
        "period_start": "2026-06-02",
        "period_end": "2026-06-15",
        "data_sources": ["notes", "tickets"],
    },
]

# Account-level headlines — customer_name/account_name must match an
# Account already seeded by seed_demo_accounts (under that customer).
DEMO_ACCOUNT_HEADLINES = [
    # The two cards HEADLINES_DATA used to hardcode, now Apple EMEA's own.
    {
        "customer_name": "Apple Inc",
        "account_name": "Apple EMEA",
        "kind": Headline.Kind.SUMMARY,
        "title": "TL;DR (Last 3 months)",
        "content": "Apple EMEA Retail Operations account shows exceptional "
        "health with strong renewal momentum throughout December 2025 and "
        "January 2026. The account maintains 96% utilization (1,150/1,200 "
        "users), £135K ARR, and 91 health score with consistent positive "
        "sentiment. December focused heavily on renewal preparation and "
        "expansion planning, with multiple stakeholders coordinating on "
        "Analytics Suite expansion and Integrations Module evaluation. January "
        "shifted to renewal execution and advanced optimization discussions. "
        "No risks identified - account positioned for successful renewal with "
        "significant growth opportunities.",
        "time_period_label": "Last 3 months",
        "data_sources": ALL_SOURCES,
    },
    {
        "customer_name": "Apple Inc",
        "account_name": "Apple EMEA",
        "title": "Apple EMEA Retail Operations Renewal and Expansion",
        "content": "Comprehensive renewal process for Apple's EMEA Retail "
        "Operations showing exceptional account health with 96% utilization "
        "and strong expansion interest. Daniel from Revenact coordinated "
        "renewal documentation and expansion modeling for Analytics Suite and "
        "Integrations Module, while Priya and Leo from Apple consolidated "
        "usage trends and conducted internal reviews. The account "
        "demonstrates consistent positive metrics with £135K ARR, 91 health "
        "score, and teams actively advocating for broader rollouts. Renewal "
        "positioned for success with meaningful growth opportunities "
        "identified.",
        "status": Headline.Status.OPEN,
        "period_start": "2025-11-20",
        "period_end": "2026-01-21",
        "data_sources": ["notes", "emails", "call_transcripts"],
    },
    {
        "customer_name": "Apple Inc",
        "account_name": "North America Enterprise",
        "kind": Headline.Kind.SUMMARY,
        "title": "TL;DR (Last 3 months)",
        "content": "North America Enterprise is the healthiest of Apple's "
        "three regions and the furthest along technically. The executive "
        "sponsor is satisfied and has one clear ask: AI dashboards GA by end "
        "of Q2. Technical handoff is fully complete. No blockers were raised "
        "in the period.",
        "time_period_label": "Last 3 months",
        "data_sources": ["notes", "emails"],
    },
    {
        "customer_name": "Apple Inc",
        "account_name": "North America Enterprise",
        "title": "AI Dashboards Requested for Q2 GA",
        "content": "Tim, the executive sponsor, expressed high satisfaction "
        "with the platform in his meeting with Edgar Holmes and named AI "
        "dashboards by end of Q2 as the one thing he wants. Natalie Reyes "
        "closed out the technical handoff in the same period — SSO "
        "configured, API keys rotated, security scan passed, IT signed off on "
        "enterprise compliance. The ask is a roadmap commitment, not a "
        "remediation.",
        "status": Headline.Status.OPEN,
        "period_start": "2026-03-10",
        "period_end": "2026-03-20",
        "data_sources": ["notes", "emails"],
    },
    {
        "customer_name": "Apple Inc",
        "account_name": "Apple APAC",
        "title": "APAC Regional Rollout Two-Thirds Complete",
        "content": "Three of four APAC offices are fully onboarded, with the "
        "fourth scheduled for the following month, per Natalie Reyes's "
        "rollout notes. No escalations were raised during the rollout. The "
        "account is tracking behind EMEA on utilization purely because it "
        "started later, not because of adoption resistance.",
        "status": Headline.Status.IN_PROGRESS,
        "period_start": "2026-06-20",
        "period_end": "2026-06-20",
        "data_sources": ["notes"],
    },
    {
        "customer_name": "Kraft Heinz",
        "account_name": "Heinz Europe",
        "title": "Europe Renewal Date Confirmed, No Blockers",
        "content": "Europe leadership confirmed the target renewal date with "
        "Edgar Holmes and raised no outstanding blockers. The APAC Division "
        "saw a noticeable adoption uptick after its regional rollout "
        "completed, which strengthens the wider Kraft Heinz renewal position.",
        "status": Headline.Status.OPEN,
        "period_start": "2026-07-15",
        "period_end": "2026-08-04",
        "data_sources": ["notes"],
    },
    {
        "customer_name": "Shopify",
        "account_name": "Shopify Plus",
        "title": "Checkout Errors Resolved by Rate-Limit Increase",
        "content": "Checkout errors on the Plus tier were traced to a "
        "peak-load rate limit rather than a defect; the limit was raised for "
        "the tier and errors stopped. Procurement separately confirmed budget "
        "approval is on track for this quarter's renewal cycle, so the "
        "incident did not affect commercial momentum.",
        "status": Headline.Status.CLOSED,
        "period_start": "2026-06-27",
        "period_end": "2026-08-13",
        "data_sources": ["notes", "tickets"],
    },
    {
        "customer_name": "Hyatt Hotels Corporation",
        "account_name": "Hyatt Americas",
        "title": "Renewal Proposal With Procurement",
        "content": "The renewal proposal went to procurement and is awaiting "
        "sign-off before end of quarter. Earlier in the period a booking API "
        "incident was closed out — root cause a stale cache entry during a "
        "deploy, with a new alert added to catch it earlier next time. The "
        "EMEA & APAC region hit its first onboarding milestone ahead of "
        "schedule.",
        "status": Headline.Status.OPEN,
        "period_start": "2026-06-24",
        "period_end": "2026-07-20",
        "data_sources": ["notes", "emails", "tickets"],
    },
]


class Command(BaseCommand):
    help = "Seeds demo Headline rows under existing demo Customers/Accounts."

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

        for row in DEMO_CUSTOMER_HEADLINES:
            try:
                customer = Customer.objects.get(organisation=org, name=row["customer_name"])
            except Customer.DoesNotExist:
                self.stderr.write(
                    f"  skipping headline — no customer {row['customer_name']!r} in {org.name}."
                )
                skipped += 1
                continue

            _, was_created = Headline.objects.update_or_create(
                customer=customer,
                title=row["title"],
                defaults=self._defaults(row),
            )
            created += was_created
            updated += not was_created

        for row in DEMO_ACCOUNT_HEADLINES:
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
                    f"  skipping headline — no account {row['account_name']!r} under "
                    f"{row['customer_name']!r} in {org.name}."
                )
                skipped += 1
                continue

            _, was_created = Headline.objects.update_or_create(
                account=account,
                title=row["title"],
                defaults=self._defaults(row),
            )
            created += was_created
            updated += not was_created

        self.stdout.write(
            self.style.SUCCESS(
                f"{org.name}: created {created}, updated {updated}, skipped {skipped} headline(s)."
            )
        )

    @staticmethod
    def _defaults(row):
        """Everything but the parent and the title, which are the
        update_or_create match keys. `generated_at` is deliberately
        left unset — see this module's own docstring."""
        return {
            "kind": row.get("kind", Headline.Kind.HEADLINE),
            "content": row["content"],
            "status": row.get("status", ""),
            "period_start": row.get("period_start"),
            "period_end": row.get("period_end"),
            "time_period_label": row.get("time_period_label", ""),
            "data_sources": row.get("data_sources", []),
        }
