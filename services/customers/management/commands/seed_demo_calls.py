"""Dev/demo convenience — not part of the product. Seeds Call rows so the AI
Trending Topics dashboard's source donut has a real "Call" slice and its
Detailed Activity Breakdown has calls in it — run after seed_demo_customers,
seed_demo_accounts and seed_demo_connectors.

The hand-written rows keep the two calls CallSenseTab.tsx's own CALLSENSE_DATA
mock named (the Apple EMEA renewal check-ins), so the demo shows the same
content that mock did; the rest are generated from templates across every
seeded company, because two calls across one account would leave the donut with
a sliver and the weekly trend with a single point.

Generated titles are drawn from a fixed list of real CS call shapes — renewal
reviews, onboarding kickoffs, escalation post-mortems — rather than "Call 37",
so the classifier has something to classify and the table reads like a book of
business rather than a fixture.

Deterministic and idempotent: one seeded RNG, and generated rows are matched by
(parent, title) — not by date, which is computed from today and would make every
re-run a fresh set of rows — so re-running updates instead of duplicating.
Every generated call is attributed to the org-wide Zoom connector that
seed_demo_connectors creates, or to none at all if it doesn't exist yet —
never to a recorder that doesn't cover the company, which is the invariant
Call.clean() enforces.

Usage:
    python manage.py seed_demo_calls --org-email alice@acme.io
    python manage.py seed_demo_calls --org-email alice@acme.io --volume 120
"""

import random
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from services.accounts.models import User
from services.connectors.models import Connector
from services.customers.models import Account, Call, Customer

#: The two calls CallSenseTab's own mock named, so the demo keeps showing them.
DEMO_ACCOUNT_CALLS = [
    {
        "customer_name": "Apple Inc",
        "account_name": "Apple EMEA",
        "title": "EMEA Retail - Renewal Readiness Check-in",
        "host_name": "Chamath Gamage",
        "occurred_at": "2026-01-21 17:12",
        "duration_minutes": 45,
        "summary": (
            "Walked the EMEA Retail team through renewal readiness. Adoption is "
            "strong across stores; they asked about fine-tuning advanced "
            "workflows to get more out of the platform before renewal."
        ),
        "links": 2,
    },
    {
        "customer_name": "Apple Inc",
        "account_name": "Apple EMEA",
        "title": "Renewal & Expansion Review - Apple EMEA Retail Operations",
        "host_name": "Chamath Gamage",
        "occurred_at": "2025-12-05 17:12",
        "duration_minutes": 60,
        "summary": (
            "Reviewed expansion into two more regions alongside the renewal. "
            "No blockers raised; procurement wants the paperwork a month early."
        ),
        "links": 1,
    },
]

#: Generated call shapes. `(title, summary)` pairs, written so a classifier has
#: real subject matter to read — a title alone is usually enough, but a call
#: with no summary is also a real case and some of these leave it blank.
CALL_TEMPLATES = [
    (
        "Quarterly Business Review",
        "Walked through usage, open tickets and the roadmap for next quarter. "
        "Sponsor happy with progress; asked for a dashboard of their own.",
    ),
    (
        "Onboarding Kickoff",
        "Set up the implementation plan and named owners on both sides. They "
        "need help importing three years of historical records.",
    ),
    (
        "Escalation Post-mortem",
        "Walked through the outage timeline. They were measured about it but "
        "want to see alerting change before they sign off.",
    ),
    (
        "Integration Troubleshooting Session",
        "Their webhook endpoint has been rejecting our retries. Engineering "
        "joined; root cause looks like their proxy, not our delivery.",
    ),
    (
        "Renewal Readiness Check-in",
        "Renewal is in scope and the sponsor is supportive. Procurement is the "
        "long pole; nothing product-related is blocking it.",
    ),
    (
        "Training Session - New Admin Team",
        "Trained four new admins on permissions and reporting. They asked for "
        "a recording to share with the wider team.",
    ),
    (
        "Expansion Discovery Call",
        "Explored adding two more business units. They want to see seat-level "
        "utilisation before committing budget.",
    ),
    (
        "Executive Alignment Call",
        "Exec sponsor reiterated the security review timeline. Asked for our "
        "data residency commitments in writing.",
    ),
    (
        "Monthly Adoption Review",
        "Logins are up, but report exports keep timing out for their largest "
        "dataset, which they raised again and clearly find frustrating.",
    ),
    ("Support Handover Call", ""),
    ("Ad-hoc Check-in", ""),
    (
        "Workflow Automation Design Review",
        "Designed three automations with their ops lead. One existing rule is "
        "firing twice and needs fixing first.",
    ),
]

HOSTS = [
    "Chamath Gamage",
    "Melak Anbessa",
    "Justin Middleton",
    "Joey Gilkey",
    "Gerry Hill",
]


class Command(BaseCommand):
    help = "Seeds demo Call rows for the target organisation."

    def add_arguments(self, parser):
        parser.add_argument(
            "--org-email",
            required=True,
            help="Email of a user in the target organisation (e.g. the admin who signed up).",
        )
        parser.add_argument(
            "--volume",
            type=int,
            default=180,
            help="How many generated calls to add on top of the hand-written ones.",
        )

    def handle(self, *args, **options):
        try:
            caller = User.objects.get(email=options["org_email"])
        except User.DoesNotExist as exc:
            raise CommandError(f"No user with email {options['org_email']!r}.") from exc

        org = caller.organisation
        volume = options["volume"]
        if volume < 0:
            raise CommandError("--volume can't be negative.")

        # Org-wide, so it covers every company — Call.clean()'s invariant holds
        # for any parent. None when connectors haven't been seeded, which makes
        # the calls "logged in Revenact" rather than invalid.
        recorder = Connector.objects.filter(
            organisation=org, provider=Connector.Provider.ZOOM
        ).first()

        created, updated, skipped = 0, 0, 0

        for row in DEMO_ACCOUNT_CALLS:
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
                    f"  skipping call — no account {row['account_name']!r} under "
                    f"{row['customer_name']!r} in {org.name}."
                )
                skipped += 1
                continue

            occurred_at = timezone.make_aware(
                timezone.datetime.strptime(row["occurred_at"], "%Y-%m-%d %H:%M")
            )
            _, was_created = Call.objects.update_or_create(
                account=account,
                title=row["title"],
                occurred_at=occurred_at,
                defaults={
                    "host_name": row["host_name"],
                    "duration_minutes": row["duration_minutes"],
                    "summary": row["summary"],
                    "links": row["links"],
                    "connector": recorder,
                },
            )
            created += was_created
            updated += not was_created

        rng = random.Random(20260912)
        now = timezone.now()
        parents = [{"customer": c} for c in Customer.objects.filter(organisation=org)] + [
            {"account": a} for a in Account.objects.filter(customers__organisation=org).distinct()
        ]

        generated = 0
        if volume and parents:
            for index in range(volume):
                title, summary = CALL_TEMPLATES[index % len(CALL_TEMPLATES)]
                # Spread back over roughly a year and up to today, the same
                # "runs up to the present" reasoning seed_demo_tickets
                # documents: a fixed end date makes every rolling date filter
                # render an empty chart the moment real time passes it.
                occurred_at = now - timedelta(
                    days=rng.randint(0, 360), hours=rng.randint(0, 23), minutes=rng.choice([0, 30])
                )
                # Matched on (parent, title) with the date in `defaults`: the
                # date is computed from today, so keying on it would make every
                # re-run a fresh set of rows rather than an update. The "#n"
                # suffix is what makes the title a stable key.
                _, was_created = Call.objects.update_or_create(
                    **rng.choice(parents),
                    title=f"{title} #{index + 1}",
                    defaults={
                        "occurred_at": occurred_at,
                        "host_name": rng.choice(HOSTS),
                        "duration_minutes": rng.choice([15, 25, 30, 45, 60, None]),
                        "summary": summary,
                        "links": rng.choice([0, 0, 1, 2, 3]),
                        "connector": recorder,
                    },
                )
                generated += was_created

        self.stdout.write(
            self.style.SUCCESS(
                f"{org.name}: created {created}, updated {updated}, skipped {skipped} "
                f"hand-written call(s); generated {generated}."
            )
        )
