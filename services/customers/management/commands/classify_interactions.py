"""Fill in the AI taxonomy on emails, calls and tickets, using Claude.

`ai_area` / `ai_category` / `ai_subcategory` start blank on every interaction,
and the AI Trending Topics dashboard counts only the rows that have them — so
until this runs, three of that screen's charts are empty. This is the only thing
in the codebase that writes those fields (besides a CSM correcting one in admin
and the demo seed's own deterministic values).

**Costs real money.** Every batch is a real, paid call to the configured
provider (see `services/copilot/anthropic_client.py` — Anthropic directly or the
same model through Bedrock). `--dry-run` reports how much work there is without
calling anything, and `--limit` caps a run, so the first run on a large tenant
can be a hundred records rather than fifty thousand.

**Only unclassified rows, unless asked otherwise.** The default pass skips
anything with an `ai_classified_at`, which makes it safe to run on a schedule and
keeps a hand correction from being overwritten by the next run. `--reclassify`
re-does everything in scope, for when the taxonomy itself has changed — and a
record the model then declines to place has its old answer cleared rather than
kept, because "looked at, could not say" is the honest state and the previous
answer may have been the demo seed's.

Not wired into `run_health_maintenance`, deliberately. That job is free,
idempotent and safe to run hourly; this one spends money per row, and bundling
them would mean every health tick billing a provider. Schedule it separately,
less often, and with `--limit`:

    # nightly, a few hundred rows at a time
    30 1 * * *  cd /srv/revenact && venv/bin/python manage.py classify_interactions --limit 300

Usage:
    python manage.py classify_interactions --dry-run
    python manage.py classify_interactions --org-email alice@acme.io --limit 100
    python manage.py classify_interactions --only email --reclassify
"""

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from services.accounts.models import User
from services.copilot.anthropic_client import (
    BudgetExceeded,
    CopilotNotConfigured,
    CopilotRequestFailed,
)
from services.customers.classification import (
    BATCH_SIZE,
    apply_classification,
    classify_batch,
    clear_classification,
)
from services.customers.models import Call, Email, Ticket

#: The three interaction types the dashboard counts, by the name `--only` takes.
MODELS = {"email": Email, "call": Call, "ticket": Ticket}


def _org_filter(model, organisation):
    """Rows under one organisation. Every one of these models hangs off either a
    Customer or an Account, so the org is two different joins away depending on
    which — the same either-or shape their own CheckConstraints enforce."""

    return Q(customer__organisation=organisation) | Q(account__customers__organisation=organisation)


class Command(BaseCommand):
    help = "Classify emails, calls and tickets into the AI taxonomy using Claude."

    def add_arguments(self, parser):
        parser.add_argument(
            "--org-email",
            help="Limit to one tenant, by any user's email in it. Default: every record.",
        )
        parser.add_argument(
            "--only",
            choices=sorted(MODELS),
            action="append",
            help="Limit to one record type. Repeatable. Default: all three.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="Stop after this many records. Caps the spend on a first run.",
        )
        parser.add_argument(
            "--reclassify",
            action="store_true",
            help="Re-do records that already carry a classification.",
        )
        parser.add_argument(
            "--include-corrected",
            action="store_true",
            help="With --reclassify: also redo rows a person corrected by hand. Off by default.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Count the work without calling the model or writing anything.",
        )

    def handle(self, *args, **options):
        organisation = None
        if options["org_email"]:
            try:
                user = User.objects.get(email=options["org_email"])
            except User.DoesNotExist as exc:
                raise CommandError(f"No user with email {options['org_email']!r}.") from exc
            if user.organisation is None:
                raise CommandError(f"{user.email} has no organisation.")
            organisation = user.organisation

        limit = options["limit"]
        if limit is not None and limit < 1:
            raise CommandError("--limit must be at least 1.")

        names = options["only"] or sorted(MODELS)
        pending = []
        for name in names:
            model = MODELS[name]
            queryset = model.objects.all()
            if organisation is not None:
                queryset = queryset.filter(_org_filter(model, organisation)).distinct()
            if not options["reclassify"]:
                queryset = queryset.filter(ai_classified_at__isnull=True)
            elif not options["include_corrected"]:
                # A person's correction outranks the model. A reclassify pass
                # is for a changed taxonomy or a better prompt, and neither is
                # a reason to put the model's answer back over a human's.
                queryset = queryset.filter(classification_corrected_at__isnull=True)
            # Oldest first: on a capped run, the records that have been waiting
            # longest are the ones to spend the budget on.
            pending.extend(queryset.order_by("pk"))

        if limit is not None:
            pending = pending[:limit]

        if not pending:
            self.stdout.write("Nothing to classify.")
            return

        if options["dry_run"]:
            batches = -(-len(pending) // BATCH_SIZE)
            by_type = {}
            for record in pending:
                by_type[record._meta.model_name] = by_type.get(record._meta.model_name, 0) + 1
            breakdown = ", ".join(f"{count} {name}(s)" for name, count in sorted(by_type.items()))
            self.stdout.write(
                f"Would classify {len(pending)} record(s) — {breakdown} — "
                f"in {batches} model call(s). Nothing written."
            )
            return

        classified, unplaced, failed_batches = 0, 0, 0

        for start in range(0, len(pending), BATCH_SIZE):
            batch = pending[start : start + BATCH_SIZE]
            try:
                results = classify_batch(batch, organisation=organisation)
            except BudgetExceeded as exc:
                raise CommandError(f"{exc}") from exc
            except CopilotNotConfigured as exc:
                # Nothing downstream can succeed either, so stop rather than
                # burning through every remaining batch to fail identically.
                raise CommandError(f"{exc}") from exc
            except (CopilotRequestFailed, ValueError) as exc:
                # One bad batch — a rate limit, a timeout, an unreadable answer
                # — shouldn't abandon the batches behind it. The records stay
                # unclassified, so the next run picks them up.
                self.stderr.write(f"  batch of {len(batch)} failed: {exc}")
                failed_batches += 1
                continue

            for record in batch:
                fields = results.get(f"{record._meta.model_name}:{record.pk}")
                if fields is None:
                    unplaced += 1
                    # On a reclassify pass the record already carries an
                    # answer — possibly the demo seed's, possibly a stale one.
                    # The model has now looked and could not place it, and a
                    # dashboard should not keep counting a category nobody
                    # stands behind. A default pass leaves it as it was: blank.
                    if options["reclassify"]:
                        clear_classification(record)
                    continue
                apply_classification(record, fields)
                classified += 1

        scope = organisation.name if organisation else "every organisation"
        self.stdout.write(
            self.style.SUCCESS(
                f"{scope}: classified {classified} of {len(pending)} record(s); "
                f"{unplaced} left unplaced by the model; {failed_batches} batch(es) failed."
            )
        )
