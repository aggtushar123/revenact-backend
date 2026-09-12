"""Dev/demo convenience — the demo company's org chart.

Alice (Leadership) at the top; Carl, Priya, Raj and Mei report to her;
Dana reports to Carl — so there is a manager who is not at the top, and
the hierarchy rule (services.accounts.hierarchy) has something to show.

    python manage.py seed_demo_hierarchy --org-email alice@acme.io
"""

from django.core.management.base import BaseCommand, CommandError

from services.accounts.models import User

CHART = {
    "carl@acme.io": "alice@acme.io",
    "priya@acme.io": "alice@acme.io",
    "raj@acme.io": "alice@acme.io",
    "mei@acme.io": "alice@acme.io",
    "dana@acme.io": "carl@acme.io",
    "alice@acme.io": None,
}


class Command(BaseCommand):
    help = "Sets reports_to for the demo people."

    def add_arguments(self, parser):
        parser.add_argument("--org-email", required=True)

    def handle(self, *args, **options):
        try:
            caller = User.objects.get(email=options["org_email"])
        except User.DoesNotExist as exc:
            raise CommandError(f"No user with email {options['org_email']!r}.") from exc
        org = caller.organisation
        people = {u.email: u for u in User.objects.filter(organisation=org, email__in=CHART)}
        changed = 0
        for email, boss in CHART.items():
            user = people.get(email)
            if user is None:
                self.stderr.write(f"  no user {email!r}; seed_demo_functions first.")
                continue
            manager = people.get(boss) if boss else None
            if user.reports_to_id != (manager.id if manager else None):
                user.reports_to = manager
                user.save(update_fields=["reports_to"])
                changed += 1
        self.stdout.write(self.style.SUCCESS(f"{org.name}: set {changed} reporting line(s)."))
