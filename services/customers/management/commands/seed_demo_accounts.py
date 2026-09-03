"""Dev/demo convenience — not part of the product. Seeds Account rows
under existing demo Customers (run seed_demo_customers first) so the
Organizations Details page's Accounts tab has real one-to-many data to
show instead of an empty list.

Field values are lifted from the frontend's original accountsData.ts mock
(react-ts-app/src/components/organizations/accountsData.ts), same
approach as seed_demo_customers. Two adjustments from that mock, both
noted on the Account model itself:

- `lifecycleStage` there packs a stage *and* a tier suffix into one
  string (e.g. "Live (Enterprise)") — the tier suffix is dropped here
  since Account has no tier field, just the plain lifecycle_stage.
- `aiPulseScore` there includes "At Risk"/"Critical", which aren't in
  Customer.AIPulseScore's 4 choices — both collapse to "high_risk" here,
  the closest fit.

A handful of accounts (North America Enterprise, Apple EMEA, Heinz
Europe, Hyatt EMEA & APAC) are given their own address/email/phone on
purpose, distinct from their parent Customer's — the rest are left
blank so ActivityFeed's Overview tab for a standalone Account page
demonstrates falling back to the parent's own contact info (see
mapAccountToAccountRow.ts), not just the override case.

Idempotent: matched by (customer, name), so re-running updates existing
rows instead of duplicating them. Silently skips any customer_name that
doesn't exist yet in the target organisation (e.g. seed_demo_customers
wasn't run, or was run with a company skipped).

Usage:
    python manage.py seed_demo_accounts --org-email alice@acme.io
"""

from django.core.management.base import BaseCommand, CommandError

from services.accounts.models import User
from services.customers.models import Account, Customer

# `customer_name` must match a Customer.name already seeded by
# seed_demo_customers. `owner_email` resolves to a same-organisation User
# at runtime (None leaves the account unassigned).
DEMO_ACCOUNTS = [
    {
        "customer_name": "Apple Inc",
        "name": "North America Enterprise",
        "domain": "apple.com",
        "address": "Austin, TX",
        "email": "na-enterprise@apple.com",
        "phone": "+1 (512) 555-0199",
        "owner_email": "carl@acme.io",
        "lifecycle_stage": "live",
        "health_score": "9.5",
        "pulse": [1, 1, 1, 1, 0],
        "ai_pulse_score": "very_satisfied",
        "ai_pulse_reason": (
            "Customer demonstrates high engagement (85% utilization) with strong "
            "executive sponsorship."
        ),
        "nps_score": 100,
        "csat_score": "100.00",
        "renewal_date": "2026-03-02",
        "arr": "33600.00",
    },
    {
        "customer_name": "Apple Inc",
        "name": "Apple EMEA",
        "domain": "apple.com",
        "address": "Cork, Ireland",
        "email": "emea@apple.com",
        "phone": "+353 21 428 5555",
        "owner_email": "carl@acme.io",
        "lifecycle_stage": "live",
        "health_score": "9.8",
        "pulse": [1, 1, 1, 1, 1],
        "ai_pulse_score": "very_satisfied",
        "ai_pulse_reason": (
            "Customer demonstrates high engagement (80% utilization) and expanding use cases."
        ),
        "nps_score": 90,
        "csat_score": "98.00",
        "renewal_date": "2026-06-15",
        "arr": "14400.00",
    },
    {
        "customer_name": "Apple Inc",
        "name": "Apple APAC",
        "domain": "apple.com",
        "owner_email": "carl@acme.io",
        "lifecycle_stage": "adoption",
        "health_score": "8.2",
        "pulse": [1, 1, 1, 0, 0],
        "ai_pulse_score": "satisfied",
        "ai_pulse_reason": "Steady growth but requires expansion focus into new verticals.",
        "nps_score": 50,
        "csat_score": "85.00",
        "renewal_date": "2026-09-28",
        "arr": "3200.00",
    },
    {
        "customer_name": "Pizza Hut",
        "name": "Pizza Hut US Operations",
        "domain": "pizzahut.com",
        "owner_email": "carl@acme.io",
        "lifecycle_stage": "live",
        "health_score": "1.5",
        "pulse": [2, 0, 0, 0, 0],
        "ai_pulse_score": "high_risk",
        "ai_pulse_reason": "Severe drop in engagement with unresolved critical tickets.",
        "nps_score": -90,
        "csat_score": "15.00",
        "renewal_date": "2026-06-15",
        "arr": "50400.00",
    },
    {
        "customer_name": "Pizza Hut",
        "name": "Pizza Hut International",
        "domain": "pizzahut.com",
        "owner_email": "carl@acme.io",
        "lifecycle_stage": "live",
        "health_score": "2.1",
        "pulse": [3, 2, 0, 0, 0],
        "ai_pulse_score": "high_risk",  # mock: "At Risk" — see module docstring
        "ai_pulse_reason": "Low feature adoption and declining user sentiment scores.",
        "nps_score": -70,
        "csat_score": "25.00",
        "renewal_date": "2026-06-15",
        "arr": "19200.00",
    },
    {
        "customer_name": "Kraft Heinz",
        "name": "Kraft Heinz North America",
        "domain": "kraftheinz.com",
        "owner_email": "carl@acme.io",
        "lifecycle_stage": "onboarding",
        "health_score": "8.9",
        "pulse": [1, 1, 1, 1, 1],
        "ai_pulse_score": "satisfied",
        "ai_pulse_reason": "Strong onboarding progress with high milestone completion rate.",
        "nps_score": 20,
        "csat_score": "72.00",
        "renewal_date": "2027-11-15",
        "arr": "102000.00",
    },
    {
        "customer_name": "Kraft Heinz",
        "name": "Heinz Europe",
        "domain": "kraftheinz.com",
        "address": "Amsterdam, Netherlands",
        "email": "europe@kraftheinz.com",
        "phone": "+31 20 555 0134",
        "owner_email": "carl@acme.io",
        "lifecycle_stage": "onboarding",
        "health_score": "6.4",
        "pulse": [1, 1, 3, 0, 0],
        "ai_pulse_score": "moderate",
        "ai_pulse_reason": "Technical integration delays with the ERP system.",
        "nps_score": -30,
        "csat_score": "40.00",
        "renewal_date": "2027-11-15",
        "arr": "50600.00",
    },
    {
        "customer_name": "Hyatt Hotels Corporation",
        "name": "Hyatt Americas",
        "domain": "hyatt.com",
        "owner_email": "carl@acme.io",
        "lifecycle_stage": "live",
        "health_score": "9.0",
        "pulse": [1, 1, 1, 1, 0],
        "ai_pulse_score": "satisfied",
        "ai_pulse_reason": "Strong renewal probability with LATAM expansion.",
        "nps_score": 55,
        "csat_score": "80.00",
        "renewal_date": "2026-02-02",
        "arr": "42000.00",
    },
    {
        "customer_name": "Hyatt Hotels Corporation",
        "name": "Hyatt EMEA & APAC",
        "domain": "hyatt.com",
        "address": "Zurich, Switzerland",
        "email": "emea-apac@hyatt.com",
        "phone": "+41 44 555 0177",
        "owner_email": "carl@acme.io",
        "lifecycle_stage": "adoption",
        "health_score": "5.8",
        "pulse": [3, 3, 1, 0, 0],
        "ai_pulse_score": "moderate",
        "ai_pulse_reason": "Adoption phase with mixed results across regions.",
        "nps_score": 10,
        "csat_score": "55.00",
        "renewal_date": "2026-09-02",
        "arr": "17500.00",
    },
    {
        "customer_name": "Arista Networks",
        "name": "Arista Global",
        "domain": "arista.com",
        "owner_email": "carl@acme.io",
        "lifecycle_stage": "onboarding",
        "health_score": "10.0",
        "pulse": [1, 1, 1, 1, 1],
        "ai_pulse_score": "very_satisfied",
        "ai_pulse_reason": "Exceptional onboarding velocity with proactive engagement.",
        "nps_score": 47,
        "csat_score": "80.00",
        "renewal_date": "2026-09-28",
        "arr": "101700.00",
    },
    {
        "customer_name": "Oracle",
        "name": "Oracle Cloud Division",
        "domain": "oracle.com",
        "owner_email": "carl@acme.io",
        "lifecycle_stage": "onboarding",
        "health_score": "10.0",
        "pulse": [1, 1, 1, 1, 0],
        "ai_pulse_score": "very_satisfied",
        "ai_pulse_reason": "Peak utilization and frequent beta feature participation.",
        "nps_score": 100,
        "csat_score": "100.00",
        "renewal_date": "2026-05-15",
        "arr": "0.00",
    },
    {
        "customer_name": "Salesforce",
        "name": "Salesforce Core Platform",
        "domain": "salesforce.com",
        "owner_email": "carl@acme.io",
        "lifecycle_stage": "kickoff",
        "health_score": "7.2",
        "pulse": [1, 1, 1, 0, 0],
        "ai_pulse_score": "satisfied",
        "ai_pulse_reason": "Strong exec sponsorship driving kickoff momentum.",
        "nps_score": 60,
        "csat_score": "82.00",
        "renewal_date": "2027-02-15",
        "arr": "89400.00",
    },
    {
        "customer_name": "Spotify",
        "name": "Spotify Business",
        "domain": "spotify.com",
        "owner_email": "carl@acme.io",
        "lifecycle_stage": "adoption",
        "health_score": "6.5",
        "pulse": [1, 3, 1, 0, 0],
        "ai_pulse_score": "moderate",
        "ai_pulse_reason": "Growing adoption but inconsistent engagement across teams.",
        "nps_score": 10,
        "csat_score": "58.00",
        "renewal_date": "2026-08-10",
        "arr": "38400.00",
    },
    {
        "customer_name": "Stripe",
        "name": "Stripe Payments",
        "domain": "stripe.com",
        "owner_email": "carl@acme.io",
        "lifecycle_stage": "renewal",
        "health_score": "8.1",
        "pulse": [1, 1, 1, 1, 0],
        "ai_pulse_score": "satisfied",
        "ai_pulse_reason": "Strong ROI metrics heading into renewal cycle.",
        "nps_score": 55,
        "csat_score": "76.00",
        "renewal_date": "2026-05-05",
        "arr": "112000.00",
    },
    {
        "customer_name": "WeWork",
        "name": "WeWork US",
        "domain": "wework.com",
        "owner_email": "carl@acme.io",
        "lifecycle_stage": "churn",
        "health_score": "1.2",
        "pulse": [2, 2, 0, 0, 0],
        "ai_pulse_score": "high_risk",  # mock: "Critical" — see module docstring
        "ai_pulse_reason": "Account marked for churn due to budget constraints.",
        "nps_score": -100,
        "csat_score": "12.00",
        "renewal_date": None,
        "arr": "24000.00",
    },
    {
        "customer_name": "Shopify",
        "name": "Shopify Plus",
        "domain": "shopify.com",
        "owner_email": "carl@acme.io",
        "lifecycle_stage": "expansion",
        "health_score": "9.5",
        "pulse": [1, 1, 1, 1, 1],
        "ai_pulse_score": "very_satisfied",
        "ai_pulse_reason": "Expanding license count with APAC team onboarding.",
        "nps_score": 85,
        "csat_score": "94.00",
        "renewal_date": "2026-07-12",
        "arr": "175000.00",
    },
]


class Command(BaseCommand):
    help = "Seeds demo Account rows (from accountsData.ts) under existing demo Customers."

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

        for row in DEMO_ACCOUNTS:
            try:
                customer = Customer.objects.get(organisation=org, name=row["customer_name"])
            except Customer.DoesNotExist:
                self.stderr.write(
                    f"  skipping {row['name']!r} — no customer {row['customer_name']!r} in "
                    f"{org.name} (run seed_demo_customers first?)."
                )
                skipped += 1
                continue

            data = {
                k: v for k, v in row.items() if k not in ("customer_name", "name", "owner_email")
            }
            owner_email = row.get("owner_email")
            if owner_email:
                try:
                    data["owner"] = User.objects.get(email=owner_email, organisation=org)
                except User.DoesNotExist:
                    self.stderr.write(
                        f"  skipping owner assignment for {row['name']!r} — "
                        f"no user {owner_email!r} in {org.name}."
                    )

            # Account.customers is a many-to-many now (see that model's
            # own docstring), so update_or_create(customer=..., ...)
            # no longer works -- an M2M field can't be a constructor
            # kwarg. Matched by (customer, name) same as before, just
            # via an explicit filter + add() instead.
            account = Account.objects.filter(customers=customer, name=row["name"]).first()
            was_created = account is None
            if was_created:
                account = Account.objects.create(name=row["name"], **data)
            else:
                for field, value in data.items():
                    setattr(account, field, value)
                account.save()
            account.customers.add(customer)
            created += was_created
            updated += not was_created

        self.stdout.write(
            self.style.SUCCESS(
                f"{org.name}: created {created}, updated {updated}, skipped {skipped} account(s)."
            )
        )
