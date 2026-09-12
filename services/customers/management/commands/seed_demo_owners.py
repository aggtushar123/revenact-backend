"""Dev/demo convenience — not part of the product. Gives the demo book a
second owner.

Every seeded customer belonged to Carl, so anything the brain cuts by
owner — the owner column of the knowledge graph, "ARR at risk by owner",
the Ops agent's choice of assignee, the facilitator's "who took it on" —
had one member and proved nothing. This creates Dana (a CSM, same demo
password as everyone else) and moves a fixed slice of the book to her:
enough accounts, on both products and across the health bands, that the
owner cut shows a real split. seed_demo_customers names the same owners,
so a fresh seed and this command agree.

Idempotent: the user is matched by email, the customers by name, and
re-running changes nothing the second time.

Usage:
    python manage.py seed_demo_owners --org-email alice@acme.io
"""

from django.core.management.base import BaseCommand, CommandError

from services.accounts.models import User
from services.customers.models import Customer

SECOND_OWNER = {"email": "dana@acme.io", "name": "Dana CSM", "password": "supersecret1"}

#: Moved to Dana. Spotify and Zoom are on Analytics Suite, Hyatt, Arista, Stripe
#: and Shopify on the others, so both products end up shared between owners.
DANA_BOOK = (
    "Hyatt Hotels Corporation",
    "Arista Networks",
    "Spotify",
    "Stripe",
    "Shopify",
    "Zoom",
)


class Command(BaseCommand):
    help = "Adds a second CSM to the demo organisation and moves a slice of the book to her."

    def add_arguments(self, parser):
        parser.add_argument(
            "--org-email",
            required=True,
            help="Email of a user in the target organisation (e.g. the admin who signed up).",
        )

    def handle(self, *args, **options):
        try:
            caller = User.objects.get(email=options["org_email"])
        except User.DoesNotExist as exc:
            raise CommandError(f"No user with email {options['org_email']!r}.") from exc
        org = caller.organisation

        dana = User.objects.filter(email=SECOND_OWNER["email"]).first()
        created_user = dana is None
        if dana is None:
            dana = User.objects.create_user(
                email=SECOND_OWNER["email"],
                password=SECOND_OWNER["password"],
                name=SECOND_OWNER["name"],
                organisation=org,
                role=User.Role.CSM,
            )
        elif dana.organisation_id != org.id:
            raise CommandError(f"{SECOND_OWNER['email']} exists in another organisation.")

        moved = 0
        for name in DANA_BOOK:
            customer = Customer.objects.filter(organisation=org, name=name).first()
            if customer is None:
                self.stderr.write(
                    f"  no customer {name!r} in {org.name}; seed_demo_customers first."
                )
                continue
            if customer.owner_id != dana.id:
                customer.owner = dana
                customer.save(update_fields=["owner"])
                moved += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"{org.name}: {'created' if created_user else 'found'} {dana.name}; "
                f"moved {moved} customer(s) to her."
            )
        )
