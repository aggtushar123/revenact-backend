"""Dev/demo convenience — not part of the product. Seeds an organisation
with realistic Customer rows so the Organizations page (table +
MetricsPanel) has enough variety to look real instead of the 2-3 rows a
freshly reset dev DB starts with.

The field values are lifted straight from the frontend's original
tableData.ts mock (react-ts-app/src/components/organizations/tableData.ts)
so this replicates the same health/lifecycle/NPS spread that data was
designed to demo. Apple Inc, Pizza Hut, and WeWork (also from that mock)
are skipped by default since they tend to already exist from earlier
manual seeding — pass --include-existing to add them too.

Idempotent: matched by (organisation, name), so re-running updates the
existing row instead of duplicating it.

Usage:
    python manage.py seed_demo_customers --org-email alice@acme.io
"""

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from services.accounts.models import Organisation, User
from services.customers.models import Customer, Product

# One entry per company. `owner_email` is resolved to a same-organisation
# User at runtime (None leaves the customer unassigned, same as the mock's
# own "Unassigned" rows).
DEMO_CUSTOMERS = [
    {
        "name": "Apple Inc",
        "domain": "apple.com",
        "address": "Cupertino, CA",
        "email": "contact@apple.com",
        "phone": "+1 (408) 996-1010",
        "owner_email": "carl@acme.io",
        "lifecycle_stage": "live",
        "health_score": "9.3",
        "pulse": [1, 1, 1, 1, 1],
        "ai_pulse_value": 5,
        "csm_pulse_score": 5,
        "ai_pulse_reason": (
            "Consistent high feature adoption and proactive usage across all key metrics."
        ),
        "nps_score": 100,
        "csat_score": "100.00",
        "joined_date": "2024-10-19",
        "renewal_date": "2026-03-02",
        "contract_start_date": "2024-10-26",
        "contract_end_date": "2025-08-12",
        "arr_billed_at_account": "51200.00",
        "arr_billed_at_hq": "128300.00",
        "implementation_fee": "70000.00",
        "total_contract_value": "179500.00",
        "total_forecasted_renewal_revenue": "188475.00",
        "primary_product": "Product A",
        "additional_products_count": 3,
        "top_source_channel": "Talent Pool Re-engage",
        "total_contracted_seats": 560,
        "total_active_seats": 471,
        "total_hires": 124,
        "scope_web_app": "N/A",
        "ces_percentage": "98.00",
    },
    {
        "name": "Pizza Hut",
        "domain": "pizzahut.com",
        "address": "Plano, TX",
        "email": "contact@pizzahut.com",
        "phone": "+1 (972) 338-7700",
        "owner_email": "carl@acme.io",
        "lifecycle_stage": "live",
        "health_score": "1.8",
        "pulse": [2, 0, 0, 0, 0],
        "ai_pulse_value": 1,
        "csm_pulse_score": 2,
        "ai_pulse_reason": (
            "Significant drop in active users and multiple unresolved "
            "high-severity support tickets."
        ),
        "nps_score": -80,
        "csat_score": "20.00",
        "joined_date": "2024-09-25",
        "renewal_date": "2026-06-15",
        "arr_billed_at_account": "69600.00",
        "arr_billed_at_hq": "0.00",
        "implementation_fee": "60000.00",
        "total_contract_value": "69600.00",
        "total_forecasted_renewal_revenue": "73080.00",
        "primary_product": "Product B",
        "additional_products_count": 2,
        "top_source_channel": "University Portal",
        "total_contracted_seats": 543,
        "total_active_seats": 88,
        "total_hires": 42,
        "scope_web_app": "N/A",
        "ces_percentage": "45.00",
    },
    {
        "name": "Kraft Heinz",
        "domain": "kraftheinz.com",
        "address": "Chicago, IL",
        "email": "contact@kraftheinz.com",
        "phone": "+1 (847) 646-2000",
        "owner_email": "carl@acme.io",
        "lifecycle_stage": "onboarding",
        "health_score": "8.6",
        "pulse": [1, 1, 1, 1, 1],
        "ai_pulse_value": 3,
        "csm_pulse_score": 3,
        "ai_pulse_reason": (
            "Successful milestone completion but technical integration delays "
            "causing moderate friction."
        ),
        "nps_score": -17,
        "csat_score": "47.80",
        "joined_date": "2024-04-24",
        "renewal_date": "2027-11-15",
        "contract_start_date": "2024-10-31",
        "contract_end_date": "2027-11-22",
        "arr_billed_at_account": "152600.00",
        "arr_billed_at_hq": "167800.00",
        "implementation_fee": "85000.00",
        "total_contract_value": "320400.00",
        "total_forecasted_renewal_revenue": "336420.00",
        "primary_product": "Integrations Module",
        "top_source_channel": "Google Search",
        "total_contracted_seats": 1071,
        "total_active_seats": 940,
        "total_hires": 56,
        "scope_web_app": "N/A",
        "ces_percentage": "75.00",
    },
    {
        "name": "Hyatt Hotels Corporation",
        "domain": "hyatt.com",
        "address": "Chicago, IL",
        "email": "contact@hyatt.com",
        "phone": "+1 (312) 750-1234",
        "owner_email": "carl@acme.io",
        "lifecycle_stage": "live",
        "health_score": "8.8",
        "pulse": [3, 3, 3, 0, 0],
        "ai_pulse_value": 4,
        "csm_pulse_score": 4,
        "ai_pulse_reason": (
            "High renewal probability backed by strong expansion into the LATAM region."
        ),
        "nps_score": 40,
        "csat_score": "73.30",
        "joined_date": "2023-03-26",
        "renewal_date": "2026-02-02",
        "contract_start_date": "2024-02-24",
        "contract_end_date": "2026-09-02",
        "arr_billed_at_account": "59500.00",
        "arr_billed_at_hq": "101900.00",
        "implementation_fee": "40000.00",
        "total_contract_value": "161400.00",
        "total_forecasted_renewal_revenue": "169470.00",
        "primary_product": "Product C",
        "top_source_channel": "Talent Pool Re-engage",
        "total_contracted_seats": 1041,
        "total_active_seats": 822,
        "total_hires": 19,
        "scope_web_app": "N/A",
        "ces_percentage": "88.00",
    },
    {
        "name": "Arista Networks",
        "domain": "arista.com",
        "address": "Santa Clara, CA",
        "email": "contact@arista.com",
        "phone": "+1 (408) 547-5500",
        "owner_email": "carl@acme.io",
        "lifecycle_stage": "onboarding",
        "health_score": "10.0",
        "pulse": [1, 1, 1, 1, 1],
        "ai_pulse_value": 4,
        "csm_pulse_score": 1,
        "ai_pulse_reason": (
            "Steady onboarding progress with high sentiment scores from the execution team."
        ),
        "nps_score": 47,
        "csat_score": "80.00",
        "joined_date": "2023-01-01",
        "renewal_date": "2026-09-28",
        "contract_start_date": "2023-12-16",
        "contract_end_date": "2026-10-05",
        "arr_billed_at_account": "101700.00",
        "arr_billed_at_hq": "131100.00",
        "implementation_fee": "65000.00",
        "total_contract_value": "232800.00",
        "total_forecasted_renewal_revenue": "244440.00",
        "primary_product": "Product C",
        "additional_products_count": 1,
        "top_source_channel": "University Portal",
        "total_contracted_seats": 607,
        "total_active_seats": 412,
        "total_hires": 30,
        "scope_web_app": "N/A",
        "ces_percentage": "91.00",
    },
    {
        "name": "Oracle",
        "domain": "oracle.com",
        "address": "Austin, TX",
        "email": "contact@oracle.com",
        "phone": "+1 (650) 506-7000",
        "owner_email": None,
        "lifecycle_stage": "onboarding",
        "health_score": "10.0",
        "pulse": [1, 1, 1, 1, 0],
        "ai_pulse_value": 5,
        "csm_pulse_score": 5,
        "ai_pulse_reason": (
            "Peak platform utilization and frequent participation in our beta features program."
        ),
        "nps_score": 100,
        "csat_score": "100.00",
        "joined_date": "2025-06-01",
        "renewal_date": "2026-05-15",
        "contract_start_date": "2025-06-10",
        "contract_end_date": "2026-06-10",
        "arr_billed_at_account": "0.00",
        "arr_billed_at_hq": "0.00",
        "implementation_fee": "20000.00",
        "total_contract_value": "0.00",
        "total_forecasted_renewal_revenue": "0.00",
        "primary_product": "Integrations Module",
        "additional_products_count": 2,
        "top_source_channel": "Indeed",
        "total_contracted_seats": 0,
        "total_active_seats": 0,
        "total_hires": 0,
        "scope_web_app": "N/A",
        "ces_percentage": "96.00",
    },
    {
        "name": "Salesforce",
        "domain": "salesforce.com",
        "address": "San Francisco, CA",
        "email": "contact@salesforce.com",
        "phone": "+1 (415) 901-7000",
        "owner_email": "carl@acme.io",
        "lifecycle_stage": "kickoff",
        "health_score": "7.2",
        "pulse": [1, 1, 1, 0, 0],
        "ai_pulse_value": 4,
        "csm_pulse_score": 4,
        "ai_pulse_reason": (
            "Strong executive sponsorship with clear success criteria defined during kickoff phase."
        ),
        "nps_score": 60,
        "csat_score": "82.00",
        "joined_date": "2025-02-15",
        "renewal_date": "2027-02-15",
        "contract_start_date": "2025-03-01",
        "contract_end_date": "2027-02-28",
        "arr_billed_at_account": "89400.00",
        "arr_billed_at_hq": "89400.00",
        "implementation_fee": "55000.00",
        "total_contract_value": "233800.00",
        "total_forecasted_renewal_revenue": "245490.00",
        "primary_product": "Product A",
        "additional_products_count": 2,
        "top_source_channel": "Partner Referral",
        "total_contracted_seats": 820,
        "total_active_seats": 340,
        "total_hires": 15,
        "scope_web_app": "N/A",
        "ces_percentage": "80.00",
    },
    {
        "name": "Spotify",
        "domain": "spotify.com",
        "address": "Stockholm, SE",
        "email": "contact@spotify.com",
        "phone": "+46 8 452 30 00",
        "owner_email": "carl@acme.io",
        "lifecycle_stage": "adoption",
        "health_score": "6.5",
        "pulse": [1, 3, 1, 0, 0],
        "ai_pulse_value": 3,
        "csm_pulse_score": 5,
        "ai_pulse_reason": (
            "Feature adoption is growing but user engagement remains inconsistent across teams."
        ),
        "nps_score": 10,
        "csat_score": "58.00",
        "joined_date": "2024-08-10",
        "renewal_date": "2026-08-10",
        "contract_start_date": "2024-08-15",
        "contract_end_date": "2026-08-14",
        "arr_billed_at_account": "38400.00",
        "arr_billed_at_hq": "38400.00",
        "implementation_fee": "25000.00",
        "total_contract_value": "101800.00",
        "total_forecasted_renewal_revenue": "106890.00",
        "primary_product": "Product B",
        "additional_products_count": 1,
        "top_source_channel": "Google Search",
        "total_contracted_seats": 380,
        "total_active_seats": 195,
        "total_hires": 28,
        "scope_web_app": "N/A",
        "ces_percentage": "62.00",
    },
    {
        "name": "Stripe",
        "domain": "stripe.com",
        "address": "South San Francisco, CA",
        "email": "contact@stripe.com",
        "phone": "+1 (888) 926-2289",
        "owner_email": "carl@acme.io",
        "lifecycle_stage": "renewal",
        "health_score": "8.1",
        "pulse": [1, 1, 1, 1, 0],
        "ai_pulse_value": 4,
        "csm_pulse_score": 1,
        "ai_pulse_reason": (
            "Upcoming renewal with strong ROI metrics and expanding use cases across departments."
        ),
        "nps_score": 55,
        "csat_score": "76.00",
        "joined_date": "2023-05-05",
        "renewal_date": "2026-05-05",
        "contract_start_date": "2023-05-10",
        "contract_end_date": "2026-05-09",
        "arr_billed_at_account": "112000.00",
        "arr_billed_at_hq": "112000.00",
        "implementation_fee": "45000.00",
        "total_contract_value": "269000.00",
        "total_forecasted_renewal_revenue": "282450.00",
        "primary_product": "Product A",
        "additional_products_count": 4,
        "top_source_channel": "Direct Sales",
        "total_contracted_seats": 900,
        "total_active_seats": 756,
        "total_hires": 67,
        "scope_web_app": "N/A",
        "ces_percentage": "89.00",
    },
    {
        "name": "WeWork",
        "domain": "wework.com",
        "address": "New York, NY",
        "email": "contact@wework.com",
        "phone": "+1 (646) 389-3922",
        "owner_email": None,
        "lifecycle_stage": "churn",
        "health_score": "1.2",
        "pulse": [2, 2, 0, 0, 0],
        "ai_pulse_value": 1,
        "csm_pulse_score": 1,
        "ai_pulse_reason": (
            "Account has been marked for churn due to budget constraints and leadership changes."
        ),
        "nps_score": -100,
        "csat_score": "12.00",
        "joined_date": "2024-01-20",
        "contract_start_date": "2024-02-01",
        "contract_end_date": "2025-01-31",
        "arr_billed_at_account": "24000.00",
        "arr_billed_at_hq": "24000.00",
        "implementation_fee": "15000.00",
        "total_contract_value": "63000.00",
        "total_forecasted_renewal_revenue": "0.00",
        "primary_product": "Product B",
        "top_source_channel": "Indeed",
        "total_contracted_seats": 150,
        "total_active_seats": 12,
        "total_hires": 5,
        "scope_web_app": "N/A",
        "ces_percentage": "18.00",
        "churn_date": "2025-01-31",
        "churn_reason": "budget",
        "churn_comment": "Leadership restructuring led to budget realignment.",
    },
    {
        "name": "Shopify",
        "domain": "shopify.com",
        "address": "Ottawa, ON",
        "email": "contact@shopify.com",
        "phone": "+1 (613) 241-2828",
        "owner_email": "carl@acme.io",
        "lifecycle_stage": "expansion",
        "health_score": "9.5",
        "pulse": [1, 1, 1, 1, 1],
        "ai_pulse_value": 5,
        "csm_pulse_score": 5,
        "ai_pulse_reason": (
            "Expanding license count and requesting additional modules for their APAC teams."
        ),
        "nps_score": 85,
        "csat_score": "94.00",
        "joined_date": "2023-07-12",
        "renewal_date": "2026-07-12",
        "contract_start_date": "2023-07-15",
        "contract_end_date": "2026-07-14",
        "arr_billed_at_account": "175000.00",
        "arr_billed_at_hq": "175000.00",
        "implementation_fee": "90000.00",
        "total_contract_value": "440000.00",
        "total_forecasted_renewal_revenue": "462000.00",
        "primary_product": "Product A",
        "additional_products_count": 5,
        "top_source_channel": "Partner Referral",
        "total_contracted_seats": 1500,
        "total_active_seats": 1380,
        "total_hires": 95,
        "scope_web_app": "N/A",
        "ces_percentage": "95.00",
    },
    {
        "name": "Twilio",
        "domain": "twilio.com",
        "address": "San Francisco, CA",
        "email": "contact@twilio.com",
        "phone": "+1 (415) 390-2337",
        "owner_email": None,
        "lifecycle_stage": "kickoff",
        "health_score": "5.5",
        "pulse": [3, 1, 0, 0, 0],
        "ai_pulse_value": 3,
        "csm_pulse_score": 3,
        "ai_pulse_reason": (
            "Initial kickoff progressing but stakeholder alignment still "
            "pending on success metrics."
        ),
        "nps_score": 0,
        "csat_score": "50.00",
        "joined_date": "2025-03-01",
        "renewal_date": "2027-03-01",
        "contract_start_date": "2025-03-10",
        "contract_end_date": "2027-03-09",
        "arr_billed_at_account": "42000.00",
        "arr_billed_at_hq": "42000.00",
        "implementation_fee": "30000.00",
        "total_contract_value": "114000.00",
        "total_forecasted_renewal_revenue": "119700.00",
        "primary_product": "Product C",
        "top_source_channel": "Google Search",
        "total_contracted_seats": 300,
        "total_active_seats": 45,
        "total_hires": 8,
        "scope_web_app": "N/A",
        "ces_percentage": "52.00",
    },
    {
        "name": "Zoom",
        "domain": "zoom.us",
        "address": "San Jose, CA",
        "email": "contact@zoom.us",
        "phone": "+1 (888) 799-9666",
        "owner_email": "carl@acme.io",
        "lifecycle_stage": "adoption",
        "health_score": "7.8",
        "pulse": [1, 1, 1, 3, 0],
        "ai_pulse_value": 4,
        "csm_pulse_score": 4,
        "ai_pulse_reason": (
            "Adoption phase going well with increasing DAU across all modules "
            "and positive exec feedback."
        ),
        "nps_score": 30,
        "csat_score": "71.00",
        "joined_date": "2024-11-20",
        "renewal_date": "2026-11-20",
        "contract_start_date": "2024-12-01",
        "contract_end_date": "2026-11-30",
        "arr_billed_at_account": "67200.00",
        "arr_billed_at_hq": "67200.00",
        "implementation_fee": "35000.00",
        "total_contract_value": "169400.00",
        "total_forecasted_renewal_revenue": "177870.00",
        "primary_product": "Product A",
        "additional_products_count": 2,
        "top_source_channel": "Direct Sales",
        "total_contracted_seats": 650,
        "total_active_seats": 430,
        "total_hires": 35,
        "scope_web_app": "N/A",
        "ces_percentage": "74.00",
    },
    {
        "name": "Uber",
        "domain": "uber.com",
        "address": "San Francisco, CA",
        "email": "contact@uber.com",
        "phone": "+1 (415) 612-8582",
        "owner_email": "carl@acme.io",
        "lifecycle_stage": "renewal",
        "health_score": "3.5",
        "pulse": [2, 3, 2, 0, 0],
        "ai_pulse_value": 1,
        "csm_pulse_score": 4,
        "ai_pulse_reason": (
            "Renewal at risk due to champion departure and active competitive "
            "evaluation with a rival platform."
        ),
        "nps_score": -45,
        "csat_score": "32.00",
        "joined_date": "2023-04-08",
        "renewal_date": "2026-04-08",
        "contract_start_date": "2023-04-15",
        "contract_end_date": "2026-04-14",
        "arr_billed_at_account": "95000.00",
        "arr_billed_at_hq": "95000.00",
        "implementation_fee": "50000.00",
        "total_contract_value": "240000.00",
        "total_forecasted_renewal_revenue": "0.00",
        "primary_product": "Product B",
        "additional_products_count": 1,
        "top_source_channel": "Talent Pool Re-engage",
        "total_contracted_seats": 700,
        "total_active_seats": 220,
        "total_hires": 18,
        "scope_web_app": "N/A",
        "ces_percentage": "35.00",
    },
]

DEFAULT_SKIP = {"Apple Inc", "Pizza Hut", "WeWork"}


def csm_pulse_stamp(index):
    """A plausible "last touched" time for a seeded CSM pulse.

    The serializer stamps `csm_pulse_modified_at` when a pulse changes through
    the API; these rows are written straight to the ORM, so without this the
    frontend's "Latest Pulse Modified" column would be empty for every seeded
    row. Spread across recent weeks rather than all identical, so the column
    shows a range of staleness the way real data would.
    """
    return timezone.now() - timedelta(days=3 + (index * 5) % 40)


#: Companies whose renewal is deliberately left in the past, and by how many
#: days. A renewal date that has passed while the customer is still active is a
#: real and common state — the deal slipped, or nobody updated the record after
#: it closed — and the Renewal Date tab has a tile for exactly that. A demo book
#: where every date is tidy would leave that tile permanently empty and the
#: worst case untested by anyone looking at the screen.
DEMO_OVERDUE_DAYS = {"WeWork": 9, "Pizza Hut": 34}


def upcoming_anniversary(anniversary, today):
    """The next occurrence of `anniversary` on or after `today`.

    The literal dates above are contract anniversaries, not one-off events: a
    renewal that passed while the customer stayed active means the contract
    renewed, and the next one is a year later. Rolling them forward at seed time
    is what keeps this book meaningful as real time moves past 2026 — the same
    trap seed_demo_tickets documents, where fixed demo dates quietly empty every
    rolling window the dashboards ask for.

    Whole years, so a March renewal stays a March renewal. Feb 29 falls back to
    Feb 28 in a non-leap year rather than raising.
    """
    rolled = anniversary
    while rolled < today:
        try:
            rolled = rolled.replace(year=rolled.year + 1)
        except ValueError:
            rolled = rolled.replace(year=rolled.year + 1, day=28)
    return rolled


class Command(BaseCommand):
    help = "Seeds demo Customer rows (from the frontend's tableData.ts mock) for an organisation."

    def add_arguments(self, parser):
        parser.add_argument(
            "--org-email",
            required=True,
            help="Email of a user in the target organisation (e.g. the admin who signed up).",
        )
        parser.add_argument(
            "--include-existing",
            action="store_true",
            help="Also (re-)seed Apple Inc/Pizza Hut/WeWork, normally skipped as already present.",
        )

    def handle(self, *args, **options):
        try:
            caller = User.objects.get(email=options["org_email"])
        except User.DoesNotExist as exc:
            raise CommandError(f"No user with email {options['org_email']!r}.") from exc

        org: Organisation = caller.organisation
        skip = set() if options["include_existing"] else DEFAULT_SKIP

        created, updated = 0, 0
        for index, row in enumerate(DEMO_CUSTOMERS):
            if row["name"] in skip:
                continue

            data = {k: v for k, v in row.items() if k not in ("name", "owner_email")}
            # The literals above still name a product as a string, because
            # that is what reads well in a fixture. Products are rows since
            # migration 0030, so the name is resolved to one here — created
            # on first use, matched case-insensitively after that, which is
            # the same rule the database enforces.
            product_name = data.pop("primary_product", "")
            if product_name:
                data["primary_product"] = self._product(org, product_name)
            if data.get("csm_pulse_score") is not None:
                data["csm_pulse_modified_at"] = csm_pulse_stamp(index)
            owner_email = row.get("owner_email")
            if owner_email:
                try:
                    data["owner"] = User.objects.get(email=owner_email, organisation=org)
                except User.DoesNotExist:
                    self.stderr.write(
                        f"  skipping owner assignment for {row['name']!r} — "
                        f"no user {owner_email!r} in {org.name}."
                    )

            customer, was_created = Customer.objects.update_or_create(
                organisation=org, name=row["name"], defaults=data
            )
            created += was_created
            updated += not was_created

        rolled, overdue = self._spread_renewals(org)

        self.stdout.write(
            self.style.SUCCESS(
                f"{org.name}: created {created}, updated {updated} customer(s); "
                f"rolled {rolled} renewal date(s) forward, left {overdue} overdue."
            )
        )

    def _product(self, org, name):
        existing = Product.objects.filter(organisation=org, name__iexact=name.strip()).first()
        if existing:
            return existing
        return Product.objects.create(organisation=org, name=name.strip())

    def _spread_renewals(self, org):
        """Move every past renewal date to its next anniversary, keeping a
        couple deliberately overdue.

        Runs after the main loop rather than inside it so the literals above
        stay readable as "this customer renews on 2 March" — the roll is a
        property of when the seed is run, not of the data."""

        today = timezone.localdate()
        rolled = overdue = 0

        for customer in Customer.objects.filter(
            organisation=org, renewal_date__isnull=False
        ):
            overdue_days = DEMO_OVERDUE_DAYS.get(customer.name)
            if overdue_days is not None:
                customer.renewal_date = today - timedelta(days=overdue_days)
                overdue += 1
            else:
                next_date = upcoming_anniversary(customer.renewal_date, today)
                if next_date == customer.renewal_date:
                    continue
                customer.renewal_date = next_date
                rolled += 1
            customer.save(update_fields=["renewal_date"])

        return rolled, overdue
