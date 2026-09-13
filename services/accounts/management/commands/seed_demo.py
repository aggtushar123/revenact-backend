"""Dev/demo convenience — the whole demo book in one idempotent command.

Creates the demo organisation (Acme Inc) with Alice (admin, leadership)
and Carl (CSM) the way signup would, then runs every seed_demo_* command
in dependency order against it. What the deployment's first boot runs, so
the team lands on a populated product; safe to run again at any time —
each seeder matches what it already made.

    python manage.py seed_demo
"""

from django.core.management import call_command
from django.core.management.base import BaseCommand

from services.accounts.models import Organisation, User

ORG_NAME = "Acme Inc"
PASSWORD = "supersecret1"
PEOPLE = [
    ("alice@acme.io", "Alice Admin", User.Role.ADMIN, User.Function.LEADERSHIP),
    ("carl@acme.io", "Carl CSM", User.Role.CSM, User.Function.CS),
]

#: Dependency order: customers before everything that hangs off them; the
#: knowledge and org-chart seeders last, since they name the people the
#: earlier ones create.
SEEDERS = [
    "seed_demo_customers",
    "seed_demo_accounts",
    "seed_demo_contacts",
    "seed_demo_connectors",
    "seed_demo_tickets",
    "seed_demo_calls",
    "seed_demo_emails",
    "seed_demo_notes",
    "seed_demo_activities",
    "seed_demo_tasks",
    "seed_demo_surveys",
    "seed_demo_opportunities",
    "seed_demo_risks",
    "seed_demo_calendar_events",
    "seed_demo_canvases",
    "seed_demo_headlines",
    "seed_demo_health_snapshots",
    "seed_demo_classifications",
    "seed_demo_owners",
    "seed_demo_functions",
    "seed_demo_hierarchy",
]


class Command(BaseCommand):
    help = "Seeds the demo organisation and everything in it (idempotent)."

    def handle(self, *args, **options):
        org = Organisation.objects.filter(name=ORG_NAME).first()
        if org is None:
            org = Organisation.objects.create(name=ORG_NAME)
            self.stdout.write(f"created organisation {ORG_NAME}")
        for email, name, role, function in PEOPLE:
            if not User.objects.filter(email=email).exists():
                User.objects.create_user(
                    email=email,
                    password=PASSWORD,
                    name=name,
                    organisation=org,
                    role=role,
                    function=function,
                )
                self.stdout.write(f"created {name} <{email}>")

        failed = []
        for name in SEEDERS:
            try:
                call_command(
                    name, org_email="alice@acme.io", stdout=self.stdout, stderr=self.stderr
                )
            except Exception as exc:  # noqa: BLE001 — one seeder must not stop the rest
                failed.append(name)
                self.stderr.write(f"{name} failed: {exc}")
        if failed:
            self.stderr.write(self.style.WARNING(f"seeders that failed: {', '.join(failed)}"))
        else:
            self.stdout.write(self.style.SUCCESS("demo organisation seeded"))
