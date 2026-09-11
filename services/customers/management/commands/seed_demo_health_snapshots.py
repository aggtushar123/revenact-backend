"""Dev/demo convenience — not part of the product. Backfills HealthSnapshot
rows for existing demo Customers and Accounts (run seed_demo_customers and
seed_demo_accounts first).

Customer.health_score only ever holds today's reading, so there is no history
to draw until something records one. Nothing writes snapshots on a schedule
yet; this stands in for that job so the Health Overview's Movement view has a
real series to chart instead of a fabricated curve.

Walks *backwards* from each row's current health, one month at a time, holding
the same grade most months and stepping a grade occasionally. Backwards rather
than forwards because the present is the known value — a forward walk would end
somewhere that disagrees with the health_score the row actually has now.

Deterministic for a given (parent, months) — seeded off the row's own primary
key — so re-running produces the same history rather than a new random one.
Idempotent: upserts by (parent, captured_on). Writes HealthSnapshot directly
rather than through capture_health_snapshot, which records a parent's *current*
readings — these are historical values that never matched the live row.

Usage:
    python manage.py seed_demo_health_snapshots --org-email alice@acme.io
    python manage.py seed_demo_health_snapshots --org-email alice@acme.io --months 24
"""

import random
from datetime import date, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from services.accounts.models import User
from services.customers.models import Account, Customer, HealthSnapshot

DEFAULT_MONTHS = 12

# How a health score tends to move month to month: mostly nowhere.
HOLD_PROBABILITY = 0.72
STEP = Decimal("1.5")
FLOOR = Decimal("0.5")
CEILING = Decimal("10.0")


def month_ends(count, today=None):
    """The last `count` completed month-end dates, oldest first.

    Completed: the month `today` falls in is excluded, since a snapshot for a
    month still in progress isn't a month-end reading.
    """
    today = today or timezone.localdate()
    ends = []
    year, month = today.year, today.month
    for _ in range(count):
        month -= 1
        if month == 0:
            year, month = year - 1, 12
        # Day 1 of the following month, minus a day, is this month's last day.
        first_of_next = date(year + (month // 12), (month % 12) + 1, 1)
        ends.append(first_of_next - timedelta(days=1))
    return list(reversed(ends))


def _walk_back(current, rng, count):
    """Health scores for `count` months ending at `current`, oldest first."""
    scores = [current]
    score = current
    for _ in range(count - 1):
        if rng.random() >= HOLD_PROBABILITY:
            score += STEP if rng.random() < 0.5 else -STEP
            score = max(FLOOR, min(CEILING, score))
        scores.append(score)
    return list(reversed(scores))


def _pulse_back(current, rng, count):
    """The same idea for a 1-5 pulse; None stays None the whole way back."""
    if current is None:
        return [None] * count
    values = [current]
    value = current
    for _ in range(count - 1):
        if rng.random() >= HOLD_PROBABILITY:
            value += 1 if rng.random() < 0.5 else -1
            value = max(1, min(5, value))
        values.append(value)
    return list(reversed(values))


class Command(BaseCommand):
    help = "Backfill HealthSnapshot history for demo Customers and Accounts."

    def add_arguments(self, parser):
        parser.add_argument("--org-email", required=True)
        parser.add_argument("--months", type=int, default=DEFAULT_MONTHS)

    def handle(self, *args, **options):
        months = options["months"]
        if months < 1:
            raise CommandError("--months must be at least 1.")

        try:
            user = User.objects.get(email=options["org_email"])
        except User.DoesNotExist as exc:
            raise CommandError(f"No user with email {options['org_email']}.") from exc

        organisation = user.organisation
        if organisation is None:
            raise CommandError(f"{user.email} has no organisation.")

        dates = month_ends(months)
        customers = list(Customer.objects.filter(organisation=organisation))
        accounts = list(Account.objects.filter(customers__organisation=organisation).distinct())

        written = 0
        for parent in (*customers, *accounts):
            written += self._backfill(parent, dates)

        self.stdout.write(
            self.style.SUCCESS(
                f"Wrote {written} snapshots across {len(customers)} customers "
                f"and {len(accounts)} accounts ({months} months to {dates[-1]})."
            )
        )

    def _backfill(self, parent, dates):
        # Seeded off the row's own pk so a re-run reproduces the same history.
        rng = random.Random(f"{type(parent).__name__}:{parent.pk}")
        count = len(dates)

        scores = _walk_back(Decimal(parent.health_score), rng, count)
        csm = _pulse_back(parent.csm_pulse_score, rng, count)
        ai = _pulse_back(parent.ai_pulse_value, rng, count)

        key = "customer" if isinstance(parent, Customer) else "account"
        for captured_on, score, csm_value, ai_value in zip(dates, scores, csm, ai, strict=True):
            HealthSnapshot.objects.update_or_create(
                **{key: parent},
                captured_on=captured_on,
                defaults={
                    "health_score": score,
                    "csm_pulse_score": csm_value,
                    "ai_pulse_value": ai_value,
                },
            )
        return count
