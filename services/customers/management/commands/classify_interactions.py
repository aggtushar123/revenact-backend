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

from datetime import datetime, time
from itertools import zip_longest

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from services.accounts.models import Organisation, User
from services.copilot.anthropic_client import (
    BudgetExceeded,
    CopilotNotConfigured,
    CopilotRequestFailed,
)
from services.customers import classification
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
#: The field each is dated by: when it happened, not when it was stored.
DATED_BY = {Email: "sent_at", Call: "occurred_at", Ticket: "opened_at"}


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
            if organisation.status != Organisation.Status.ACTIVE:
                raise CommandError(f"{organisation.name} is {organisation.status}, not active.")

        limit = options["limit"]
        if limit is not None and limit < 1:
            raise CommandError("--limit must be at least 1.")

        names = options["only"] or sorted(MODELS)
        # A suspended, archived or not-yet-active tenant is not spent on.
        organisations = (
            [organisation]
            if organisation is not None
            else Organisation.objects.filter(status=Organisation.Status.ACTIVE).order_by("pk")
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
            # A call with nothing to read costs nothing: it is marked, not sent.
            free = 0
            batches = 0
            by_type = {}
            for _org, rows in _by_org(pending):
                marked, to_read = _split_free(rows)
                free += len(marked)
                batches += -(-len(to_read) // BATCH_SIZE)
                for record in to_read:
                    name = record._meta.model_name
                    by_type[name] = by_type.get(name, 0) + 1
            count = sum(by_type.values())
            breakdown = ", ".join(f"{n} {name}(s)" for name, n in sorted(by_type.items()))
            self.stdout.write(
                f"Would classify {count} record(s)"
                + (f" — {breakdown} —" if breakdown else "")
                + f" in {batches} model call(s); "
                f"{free} call(s) would be marked not analysable without one. Nothing written."
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
        """One tenant's pending records, oldest first across the kinds: on a
        capped run, the records that have waited longest are the ones to
        spend the budget on. Only `(pk, date)` pairs are read per kind (at
        most `limit` of each); the full rows are fetched for the records
        chosen, so a capped run never holds three kinds' worth of rows."""
        keys = []
        querysets = {}
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
            querysets[model] = queryset
            dated = queryset.order_by(DATED_BY[model], "pk").values_list("pk", DATED_BY[model])
            for pk, when in dated[:limit] if limit is not None else dated:
                keys.append((_aware(when), model._meta.model_name, pk, model))
        keys.sort(key=lambda key: key[:3])
        if limit is not None:
            keys = keys[:limit]
        rows = {}
        for model, queryset in querysets.items():
            wanted = [pk for _when, _name, pk, of in keys if of is model]
            if not wanted:
                continue
            if model is Call:
                queryset = queryset.select_related("transcript")
            rows[model] = queryset.in_bulk(wanted)
        return [rows[model][pk] for _when, _name, pk, model in keys]

    def _classify(self, organisation, rows, options, totals):
        """One tenant's records, in batches. False when its budget ran out."""
        from services.customers.contact_sentiment import recompute_for_records

        # A call with nothing to read is marked without paying for a look,
        # and the people on them are recomputed once for all of them.
        marked, to_read = _split_free(rows)
        for record in marked:
            mark_not_analysable(record)
        totals["not_analysable"] += len(marked)
        if marked:
            recompute_for_records(marked)

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
                verdict, fields = classification.reading_of(record, results, len(batch))
                if verdict == classification.PLACED:
                    apply_classification(record, fields)
                    totals["classified"] += 1
                elif verdict == classification.DECLINED:
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


def _aware(when):
    """A ticket is dated by day, the rest to the second: one timeline."""
    if not isinstance(when, datetime):
        when = timezone.make_aware(datetime.combine(when, time.min))
    return when


def _split_free(rows):
    """(calls with nothing to read, everything else): the first are marked
    not analysable without a model call."""
    from services.customers.calls import has_something_to_read

    marked, to_read = [], []
    for record in rows:
        if record._meta.model_name == "call" and not has_something_to_read(record):
            marked.append(record)
        else:
            to_read.append(record)
    return marked, to_read


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
