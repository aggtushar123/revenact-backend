"""Dev/demo convenience — not part of the product. Seeds Survey rows
under existing demo Customers and Accounts so the Activity Feed's
Surveys filter and the standalone /surveys page (react-ts-app's
src/pages/surveys/SurveysPage.tsx) have real, per-entity data with a
real multi-month trend — run after seed_demo_accounts.

Every existing Customer/Account already carries an `nps_score`/
`csat_score`/`ces_percentage` from seed_demo_customers/seed_demo_accounts,
but — before this command — nothing in the database explains where
those numbers came from; Survey Tier 0's own docstring calls that out
as "a number with no provenance." This command backfills the missing
provenance: one RESPONDED Survey per non-null score field, with that
exact score, spread across the last several months so the new score
trend chart has real shape instead of one dot. `DEMO_EXTRA_SURVEYS`
below adds a small, hand-picked set of still-`sent`/`expired` rows on
top, so every Survey status shows up somewhere real (a purely
score-backfilling pass would only ever produce `responded` rows).

Idempotent: matched by (parent, survey_type, sent_at), same spirit as
seed_demo_risks's (parent, title) — re-running updates existing rows
instead of duplicating them.

Usage:
    python manage.py seed_demo_surveys --org-email alice@acme.io
"""

import random

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from services.accounts.models import User
from services.customers.models import Account, Customer, Survey

# A handful of extra, still-open-lifecycle Surveys on top of the
# score-backfill below — gives the page real `sent`/`expired` rows, not
# just `responded` ones. customer_name/account_name must match seeded
# data; CES is Customer-only, same constraint the model itself enforces.
DEMO_EXTRA_SURVEYS = [
    # A brand-new customer with no history yet — nothing has come back.
    {"customer_name": "Notion Labs", "survey_type": "nps", "status": "sent", "days_ago": 4},
    {"customer_name": "Notion Labs", "survey_type": "csat", "status": "sent", "days_ago": 4},
    # A follow-up NPS re-check mid-quarter, still awaiting a response.
    {"customer_name": "Apple Inc", "survey_type": "nps", "status": "sent", "days_ago": 6},
    {"customer_name": "Twilio", "survey_type": "csat", "status": "sent", "days_ago": 10},
    # WeWork's already-dismal NPS (-100) never even got a follow-up
    # response the one time someone tried a second check-in.
    {"customer_name": "WeWork", "survey_type": "nps", "status": "expired", "days_ago": 75},
    {
        "customer_name": "Kraft Heinz",
        "account_name": "APAC Division",
        "survey_type": "nps",
        "status": "expired",
        "days_ago": 60,
    },
]


#: Extra CSAT responses seeded per customer, on top of the exact-score one.
#: Six plus the exact one fills the seven-month rotation `next_dates` uses,
#: so every response lands in its own month.
CSAT_EXTRA_RESPONSES = 6

#: How far the extra responses sit from the customer's own average. Wide
#: enough to cross band boundaries — a spread that never leaves one band
#: would draw the same single bar the hardcoded popover did.
CSAT_SPREAD = (-30, -18, -9, 9, 18, 30)


def csat_scatter(customer):
    """Extra CSAT scores for `customer`, spread around its stored average.

    Deterministic per customer (seeded off its pk), so re-running seeds the
    same distribution rather than a new one each time. Clamped to 0-100, which
    means the set averages near the stored score rather than exactly to it —
    the exact value is carried by its own response alongside these.
    """
    rng = random.Random(f"csat:{customer.pk}")
    mean = int(round(customer.csat_score))
    offsets = rng.sample(CSAT_SPREAD, k=min(CSAT_EXTRA_RESPONSES, len(CSAT_SPREAD)))
    return [max(0, min(100, mean + offset)) for offset in offsets]


class Command(BaseCommand):
    help = "Seeds demo Survey rows under existing demo Customers/Accounts."

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

        # Round-robins each survey_type's responses across the last 7
        # months so the trend chart gets several distinct months per
        # line instead of every score landing on today.
        month_counters = {
            Survey.SurveyType.NPS: 0,
            Survey.SurveyType.CSAT: 0,
            Survey.SurveyType.CES: 0,
        }

        def next_dates(survey_type):
            months_ago = month_counters[survey_type] % 7
            month_counters[survey_type] += 1
            responded_at = today - timezone.timedelta(days=months_ago * 30 + 3)
            sent_at = responded_at - timezone.timedelta(days=5)
            return sent_at, responded_at

        def upsert_responded(*, customer=None, account=None, survey_type, score):
            sent_at, responded_at = next_dates(survey_type)
            parent = {"customer": customer} if customer else {"account": account}
            _, was_created = Survey.objects.update_or_create(
                survey_type=survey_type,
                sent_at=sent_at,
                **parent,
                defaults={
                    "status": Survey.Status.RESPONDED,
                    "score": score,
                    "responded_at": responded_at,
                },
            )
            return was_created

        for customer in Customer.objects.filter(organisation=org):
            if customer.nps_score is not None:
                was_created = upsert_responded(
                    customer=customer, survey_type=Survey.SurveyType.NPS, score=customer.nps_score
                )
                created += was_created
                updated += not was_created
            if customer.csat_score is not None:
                # The exact stored score first — that's the provenance this
                # command exists to supply.
                was_created = upsert_responded(
                    customer=customer,
                    survey_type=Survey.SurveyType.CSAT,
                    score=int(round(customer.csat_score)),
                )
                created += was_created
                updated += not was_created

                # Then a handful more scattered around it. One response is a
                # real number but not a distribution, and the Organizations
                # table's CSAT popover exists to show how answers *spread* —
                # with a single response every customer showed one 100% bar.
                for extra in csat_scatter(customer):
                    was_created = upsert_responded(
                        customer=customer,
                        survey_type=Survey.SurveyType.CSAT,
                        score=extra,
                    )
                    created += was_created
                    updated += not was_created
            if customer.ces_percentage is not None:
                was_created = upsert_responded(
                    customer=customer,
                    survey_type=Survey.SurveyType.CES,
                    score=int(round(customer.ces_percentage)),
                )
                created += was_created
                updated += not was_created

        for account in Account.objects.filter(customers__organisation=org).distinct():
            if account.nps_score is not None:
                was_created = upsert_responded(
                    account=account, survey_type=Survey.SurveyType.NPS, score=account.nps_score
                )
                created += was_created
                updated += not was_created
            if account.csat_score is not None:
                was_created = upsert_responded(
                    account=account,
                    survey_type=Survey.SurveyType.CSAT,
                    score=int(round(account.csat_score)),
                )
                created += was_created
                updated += not was_created

        for row in DEMO_EXTRA_SURVEYS:
            if "account_name" in row:
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
                        f"  skipping survey — no account {row['account_name']!r} under "
                        f"{row['customer_name']!r} in {org.name}."
                    )
                    skipped += 1
                    continue
                parent = {"account": account}
            else:
                try:
                    customer = Customer.objects.get(organisation=org, name=row["customer_name"])
                except Customer.DoesNotExist:
                    self.stderr.write(
                        f"  skipping survey — no customer {row['customer_name']!r} in {org.name}."
                    )
                    skipped += 1
                    continue
                parent = {"customer": customer}

            sent_at = today - timezone.timedelta(days=row["days_ago"])
            _, was_created = Survey.objects.update_or_create(
                survey_type=row["survey_type"],
                sent_at=sent_at,
                **parent,
                defaults={"status": row["status"]},
            )
            created += was_created
            updated += not was_created

        self.stdout.write(
            self.style.SUCCESS(
                f"{org.name}: created {created}, updated {updated}, skipped {skipped} survey(s)."
            )
        )
