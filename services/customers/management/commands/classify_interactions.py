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
them would mean every health tick billing a provider. It runs nightly on the
server, before the health job, with a cap (revenact-infra
`deploy/docker-compose.prod.yml`, the `scheduler` service):

    python manage.py classify_interactions --limit 300

**One tenant at a time.** Each organisation's records are batched and metered
on their own, so every model call is charged to the organisation whose
records it read; a capped run takes the tenants' oldest records in turn. An
organisation out of AI credits is skipped for the night and named in the
report, and the run carries on with the rest and exits cleanly.

**Calls are never guessed.** A call with no transcript, no summary and a
generic title is marked not analysable without a model call, and so is one the
model declines; either way it is not retried. Each batch recomputes the
contacts on its records, as `classify_records` does.

Usage:
    python manage.py classify_interactions --dry-run
    python manage.py classify_interactions --org-email alice@acme.io --limit 100
    python manage.py classify_interactions --only email --reclassify
"""

from itertools import zip_longest

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from services.accounts.models import Organisation, User
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
    mark_not_analysable,
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
        organisations = (
            [organisation] if organisation is not None else Organisation.objects.order_by("pk")
        )
        # Each tenant's work is gathered, batched and metered on its own: a
        # batch never mixes tenants, so every model call is charged to the
        # organisation whose records it read.
        queues = [(org, self._pending(org, names, options, limit)) for org in organisations]
        queues = [(org, rows) for org, rows in queues if rows]
        pending = _round_robin(queues, limit)

        if not pending:
            self.stdout.write("Nothing to classify.")
            return

        if options["dry_run"]:
            batches = sum(-(-len(rows) // BATCH_SIZE) for _org, rows in _by_org(pending))
            by_type = {}
            for _org, record in pending:
                by_type[record._meta.model_name] = by_type.get(record._meta.model_name, 0) + 1
            breakdown = ", ".join(f"{count} {name}(s)" for name, count in sorted(by_type.items()))
            self.stdout.write(
                f"Would classify {len(pending)} record(s) — {breakdown} — "
                f"in {batches} model call(s). Nothing written."
            )
            return

        totals = {"classified": 0, "unplaced": 0, "not_analysable": 0, "failed": 0}
        out_of_budget = []
        for org, rows in _by_org(pending):
            if not self._classify(org, rows, options, totals):
                out_of_budget.append(org.name)

        scope = organisation.name if organisation else "every organisation"
        self.stdout.write(
            self.style.SUCCESS(
                f"{scope}: classified {totals['classified']} of {len(pending)} record(s); "
                f"{totals['unplaced']} left unplaced by the model; "
                f"{totals['not_analysable']} call(s) not analysable; "
                f"{totals['failed']} batch(es) failed."
            )
        )
        if out_of_budget:
            # A clean stop, not a failure: the rest waits for next month's
            # credits or a raised budget, and the next run picks it up.
            self.stdout.write(
                "Stopped at the AI budget for: " + ", ".join(sorted(out_of_budget)) + "."
            )

    def _pending(self, organisation, names, options, limit):
        rows = []
        for name in names:
            model = MODELS[name]
            queryset = model.objects.filter(_org_filter(model, organisation)).distinct()
            if not options["reclassify"]:
                queryset = queryset.filter(ai_classified_at__isnull=True)
            elif not options["include_corrected"]:
                # A person's correction outranks the model. A reclassify pass
                # is for a changed taxonomy or a better prompt, and neither is
                # a reason to put the model's answer back over a human's.
                queryset = queryset.filter(classification_corrected_at__isnull=True)
            if model is Call:
                queryset = queryset.select_related("transcript")
            # Oldest first: on a capped run, the records that have been waiting
            # longest are the ones to spend the budget on.
            queryset = queryset.order_by("pk")
            rows.extend(queryset[:limit] if limit is not None else queryset)
        return rows

    def _classify(self, organisation, rows, options, totals):
        """One tenant's records, in batches. False when its budget ran out."""
        from services.customers.calls import has_something_to_read
        from services.customers.contact_sentiment import recompute_for_records

        # A call with nothing to read is marked without paying for a look.
        to_read = []
        for record in rows:
            if record._meta.model_name == "call" and not has_something_to_read(record):
                mark_not_analysable(record)
                totals["not_analysable"] += 1
                recompute_for_records([record])
            else:
                to_read.append(record)

        for start in range(0, len(to_read), BATCH_SIZE):
            batch = to_read[start : start + BATCH_SIZE]
            try:
                results = classify_batch(batch, organisation=organisation)
            except BudgetExceeded:
                return False
            except CopilotNotConfigured as exc:
                # Nothing downstream can succeed either, so stop rather than
                # burning through every remaining batch to fail identically.
                raise CommandError(f"{exc}") from exc
            except (CopilotRequestFailed, ValueError) as exc:
                # One bad batch — a rate limit, a timeout, an unreadable answer
                # — shouldn't abandon the batches behind it. The records stay
                # unclassified, so the next run picks them up.
                self.stderr.write(f"  batch of {len(batch)} failed: {exc}")
                totals["failed"] += 1
                continue

            touched = []
            for record in batch:
                fields = results.get(f"{record._meta.model_name}:{record.pk}")
                if fields is not None:
                    apply_classification(record, fields)
                    totals["classified"] += 1
                elif record._meta.model_name == "call" and (results or len(batch) == 1):
                    # Reaching here at all means the reply parsed (a malformed
                    # one already sent the batch to `except ValueError` above,
                    # leaving it pending). A lone call in the batch has nobody
                    # else the empty reply could be about, so it is a genuine
                    # decline — marked now, not retried (and re-billed) every
                    # night. In a batch of more than one, `results` must be
                    # non-empty too: since this record isn't the match above, a
                    # non-empty `results` placed some OTHER record, proving the
                    # model engaged with the batch rather than one poisoned
                    # transcript emptying the whole reply — which proves
                    # nothing about any particular record, so nobody in a
                    # multi-record batch is marked off the back of it.
                    mark_not_analysable(record)
                    totals["not_analysable"] += 1
                elif record._meta.model_name == "call":
                    # Left pending: an empty reply across a multi-record batch
                    # says nothing about this particular call, so it is
                    # retried rather than guessed off.
                    continue
                else:
                    totals["unplaced"] += 1
                    # On a reclassify pass the record already carries an
                    # answer — possibly the demo seed's, possibly a stale one.
                    # The model has now looked and could not place it, and a
                    # dashboard should not keep counting a category nobody
                    # stands behind. A default pass leaves it as it was: blank.
                    if options["reclassify"]:
                        clear_classification(record)
                    continue
                touched.append(record)
            # The people on these calls, emails and tickets sound different now.
            recompute_for_records(touched)
        return True


def _round_robin(queues, limit):
    """`(organisation, record)` pairs, one tenant's oldest record after
    another's in turn, capped at `limit` — so one tenant's backlog never
    spends a whole capped run while another's waits."""
    pairs = []
    for group in zip_longest(*[[(org, r) for r in rows] for org, rows in queues]):
        pairs.extend(pair for pair in group if pair is not None)
    return pairs[:limit] if limit is not None else pairs


def _by_org(pairs):
    """The pairs regrouped by tenant, each tenant's records in order."""
    grouped = {}
    for org, record in pairs:
        grouped.setdefault(org.pk, (org, []))[1].append(record)
    return list(grouped.values())
