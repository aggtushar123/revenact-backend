"""The scheduled half of account health: recalculate scores, record history.

Two things drift without anyone touching a Customer row:

- `health_score` depends on days-since-last-touch and open ticket count, so it
  goes stale on its own — an activity logged, a ticket opened, or simply a week
  passing all change the answer.
- `HealthSnapshot` is what the Movement tab charts, and nothing writes one
  except the demo seed. Without this, history stops accruing the moment the
  seeded backfill runs out.

**This is the entry point to schedule**, not celery: there is no task queue in
this project, and adding one to run a daily command would be a large piece of
infrastructure for a job that cron does. Safe to run as often as you like —
recalculation is idempotent, and the snapshot writes at most one row per
customer per month.

    # daily, a little after midnight
    5 0 * * *  cd /srv/revenact && venv/bin/python manage.py run_health_maintenance

Usage:
    python manage.py run_health_maintenance
    python manage.py run_health_maintenance --org-email alice@acme.io
    python manage.py run_health_maintenance --dry-run
"""

from datetime import date

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from services.accounts.models import User
from services.customers.models import Customer, HealthSnapshot, with_health_inputs


def last_completed_month_end(today=None):
    """The last day of the month before `today`'s.

    Snapshots are monthly because that is what the flow chart's columns are —
    a daily row would give it 365 of them, and the months would stop lining up
    across accounts.
    """
    today = today or timezone.localdate()
    first_of_this_month = date(today.year, today.month, 1)
    return first_of_this_month - timezone.timedelta(days=1)


class Command(BaseCommand):
    help = "Recalculate health scores and record the monthly health snapshot."

    def add_arguments(self, parser):
        parser.add_argument(
            "--org-email",
            help="Limit to one tenant, by any user's email in it. Default: every customer.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would happen without writing anything.",
        )

    def handle(self, *args, **options):
        queryset = Customer.objects.all()

        if options["org_email"]:
            try:
                user = User.objects.get(email=options["org_email"])
            except User.DoesNotExist as exc:
                raise CommandError(f"No user with email {options['org_email']!r}.") from exc
            if user.organisation is None:
                raise CommandError(f"{user.email} has no organisation.")
            queryset = queryset.filter(organisation=user.organisation)

        # Scores first: the snapshot should record the freshly computed value,
        # not yesterday's.
        recalculate_args = ["recalculate_health"]
        if options["org_email"]:
            recalculate_args += ["--org-email", options["org_email"]]
        if options["dry_run"]:
            recalculate_args += ["--dry-run"]
        call_command(*recalculate_args, stdout=self.stdout)

        captured_on = last_completed_month_end()
        written, already_had = self._capture(queryset, captured_on, options["dry_run"])

        verb = "would record" if options["dry_run"] else "recorded"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} {written} snapshot(s) for {captured_on}; "
                f"{already_had} customer(s) already had one."
            )
        )

    def _capture(self, queryset, captured_on, dry_run):
        """Record one snapshot per customer for `captured_on`, skipping any that
        already have one.

        Skipped rather than overwritten: that row is a record of how the month
        *ended*. Re-running a week later and upserting it would quietly replace
        it with the following month's values, and the history would drift
        forwards every time the job ran.
        """
        existing = set(
            HealthSnapshot.objects.filter(
                customer__in=queryset, captured_on=captured_on
            ).values_list("customer_id", flat=True)
        )

        written, already_had = 0, 0
        to_create = []

        for customer in with_health_inputs(queryset):
            if customer.pk in existing:
                already_had += 1
                continue
            written += 1
            if not dry_run:
                to_create.append(
                    HealthSnapshot(
                        customer=customer,
                        captured_on=captured_on,
                        health_score=customer.health_score,
                        csm_pulse_score=customer.csm_pulse_score,
                        ai_pulse_value=customer.ai_pulse_value,
                    )
                )

        if to_create:
            # One statement for the whole book; the partial unique constraint
            # still guards against a concurrent run inserting the same rows.
            HealthSnapshot.objects.bulk_create(to_create, ignore_conflicts=True)

        return written, already_had
