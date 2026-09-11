"""Recompute `Customer.health_score` from the rubric in `health.py`.

The score is refreshed whenever a customer is written through the API, but two
of its five components — Customer Touch and Support Tickets Volume — move
without the customer row itself ever being saved: an activity logged, a ticket
opened, or simply a week passing all change the answer. Nothing recalculates on
a schedule yet, so this is the entry point for that job, and for the one-off
backfill after the rubric first landed.

Customers with a `health_score_override` keep their pinned score; the command
reports how many were skipped for that reason rather than silently passing over
them.

Usage:
    python manage.py recalculate_health
    python manage.py recalculate_health --org-email alice@acme.io
    python manage.py recalculate_health --dry-run
"""

from django.core.management.base import BaseCommand, CommandError

from services.accounts.models import User
from services.customers.health import score_from
from services.customers.models import Customer, with_health_inputs


class Command(BaseCommand):
    help = "Recompute Customer.health_score from the health rubric."

    def add_arguments(self, parser):
        parser.add_argument(
            "--org-email",
            help="Limit to one tenant, by any user's email in it. Default: every customer.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing anything.",
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

        changed, unchanged, overridden, unmeasurable = 0, 0, 0, 0

        for customer in with_health_inputs(queryset):
            if customer.health_score_override is not None:
                overridden += 1
                continue

            computed = score_from(customer.health_breakdown)
            if computed is None:
                # Nothing measurable at all — leave the existing score rather
                # than dropping the customer to zero for an empty CRM row.
                unmeasurable += 1
                continue

            if computed == customer.health_score:
                unchanged += 1
                continue

            if options["dry_run"]:
                self.stdout.write(
                    f"  {customer.name}: {customer.health_score} -> {computed}"
                )
            else:
                customer.health_score = computed
                customer.save(update_fields=["health_score"])
            changed += 1

        verb = "would change" if options["dry_run"] else "changed"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} {changed}, unchanged {unchanged}, "
                f"kept {overridden} overridden, {unmeasurable} with nothing to measure."
            )
        )
