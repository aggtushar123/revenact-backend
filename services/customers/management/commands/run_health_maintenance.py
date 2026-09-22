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

from services.accounts.models import Organisation, User
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
        organisation = None

        if options["org_email"]:
            try:
                user = User.objects.get(email=options["org_email"])
            except User.DoesNotExist as exc:
                raise CommandError(f"No user with email {options['org_email']!r}.") from exc
            if user.organisation is None:
                raise CommandError(f"{user.email} has no organisation.")
            organisation = user.organisation
            queryset = queryset.filter(organisation=organisation)

        # Sentiment first: anything logged since yesterday — synced mail that
        # could not be classified at the time, tickets, calls — gets its tags
        # before the pulse and health read them. A missing model key is
        # reported, not fatal: the scores still run on what is classified.
        classify_args = ["classify_interactions"]
        if options["org_email"]:
            classify_args += ["--org-email", options["org_email"]]
        if options["dry_run"]:
            classify_args += ["--dry-run"]
        try:
            call_command(*classify_args, stdout=self.stdout)
        except CommandError as exc:
            self.stdout.write(self.style.WARNING(f"classification skipped: {exc}"))

        # Then the people: every contact's sentiment re-read from their
        # classified calls, emails and tickets, before the scores read it.
        if not options["dry_run"]:
            from services.customers.contact_sentiment import recompute_all

            changed = recompute_all(organisation)
            self.stdout.write(self.style.SUCCESS(f"recomputed sentiment for {changed} contact(s)"))

        # AI attributes marked nightly, for companies with classified activity
        # newer than their last answer. Budget or a missing key ends the pass
        # quietly inside refresh_nightly; the scores below never wait on it.
        if not options["dry_run"]:
            from services.attributes.fill import refresh_nightly

            filled = refresh_nightly(organisation)
            self.stdout.write(self.style.SUCCESS(f"filled {filled} AI attribute value(s)"))

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

        # Today's account pulse becomes one more history dot per account, once
        # a day (pulse.py; the tables draw `pulse` as the trend).
        dots, dots_had = self._record_pulses(queryset, options["dry_run"])
        self.stdout.write(
            self.style.SUCCESS(f"{verb} {dots} pulse dot(s); {dots_had} already had today's.")
        )

        # The metric layer's month-end, after the health scores it reads are
        # fresh. Same job because it is the same kind of work — free,
        # idempotent, one row per period — and one cron entry is easier to
        # keep running than two.
        from services.metrics.recording import record_period_end

        organisations = Organisation.objects.filter(
            pk__in=queryset.values_list("organisation_id", flat=True).distinct()
        )
        metric_rows, metrics_had = 0, 0
        for organisation in organisations:
            rows, had = record_period_end(organisation, captured_on, dry_run=options["dry_run"])
            metric_rows += rows
            metrics_had += had
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} {metric_rows} metric snapshot(s) for {captured_on} across "
                f"{organisations.count()} organisation(s); {metrics_had} already recorded."
            )
        )

        # Questions that have waited too long — the person asked is reminded,
        # at most once a day (services.knowledge.aging). Same cron entry.
        from services.knowledge.aging import nudge

        nudged = sum(len(nudge(o, dry_run=options["dry_run"])) for o in organisations)
        self.stdout.write(
            self.style.SUCCESS(
                f"{'would remind' if options['dry_run'] else 'reminded'} {nudged} "
                "assignee(s) of stale questions."
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

    def _record_pulses(self, customers, dry_run):
        """Append today's computed pulse category to each organisation's and
        each of its accounts' `pulse` history (last ten kept), once per day."""
        from django.utils import timezone

        from services.customers.models import (
            Account,
            Customer,
            with_customer_pulse_inputs,
            with_health_inputs,
            with_pulse_inputs,
        )

        today = timezone.localdate()
        written, already_had = 0, 0
        batches = (
            (Customer, with_customer_pulse_inputs(with_health_inputs(customers))),
            (
                Account,
                with_pulse_inputs(Account.objects.filter(customers__in=customers).distinct()),
            ),
        )
        for model, rows in batches:
            for row in rows:
                if row.pulse_recorded_on == today:
                    already_had += 1
                    continue
                written += 1
                if dry_run:
                    continue
                history = list(row.pulse or [])[-9:] + [row.account_pulse(today).category]
                model.objects.filter(pk=row.pk).update(pulse=history, pulse_recorded_on=today)
        return written, already_had
