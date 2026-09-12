"""Remind people of the questions waiting on them — see services.knowledge.aging.

Run by run_health_maintenance (the cron entry point) after the snapshots;
callable alone for a dry run.

    python manage.py nudge_open_questions [--days 3] [--dry-run]
"""

from django.core.management.base import BaseCommand

from services.accounts.models import Organisation
from services.knowledge.aging import STALE_DAYS, nudge


class Command(BaseCommand):
    help = "Remind assignees of open questions older than --days, at most once a day."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=STALE_DAYS)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        total = 0
        for organisation in Organisation.objects.all():
            due = nudge(organisation, options["days"], dry_run=options["dry_run"])
            for q in due:
                self.stdout.write(
                    f"  {organisation.name}: {q.assignee.name} <- {q.asked_by.name}: {q.text[:60]}"
                )
            total += len(due)
        verb = "would remind" if options["dry_run"] else "reminded"
        self.stdout.write(self.style.SUCCESS(f"{verb} {total} assignee(s)."))
