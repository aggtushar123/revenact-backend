"""Dev/demo convenience — not part of the product. Makes the demo company a
company: people outside customer success, responsible for accounts in
their own function, with something to say about them.

Sets Alice to Leadership and Carl and Dana to Customer Success; creates
Priya Nair (Engineering), Raj Mehta (Sales) and Mei Tanaka (Analytics),
same demo password as everyone; names them responsible for the Analytics
Suite accounts in their function; and writes a few realistic contributions
from each, so the Copilot has more than the CSM's records to answer from.

Idempotent: users matched by email, owners upserted, contributions matched
by (customer, author, body).

Usage:
    python manage.py seed_demo_functions --org-email alice@acme.io
"""

from django.core.management.base import BaseCommand, CommandError

from services.accounts.models import User
from services.customers.models import Customer
from services.knowledge.models import Contribution, FunctionOwner

PASSWORD = "supersecret1"

PEOPLE = [
    {"email": "priya@acme.io", "name": "Priya Nair", "function": User.Function.ENGINEERING},
    {"email": "raj@acme.io", "name": "Raj Mehta", "function": User.Function.SALES},
    {"email": "mei@acme.io", "name": "Mei Tanaka", "function": User.Function.ANALYTICS},
]

EXISTING = {
    "alice@acme.io": User.Function.LEADERSHIP,
    "carl@acme.io": User.Function.CS,
    "dana@acme.io": User.Function.CS,
}

#: Who answers for which account, outside customer success.
RESPONSIBLE = {
    "Pizza Hut": {
        "engineering": "priya@acme.io",
        "sales": "raj@acme.io",
        "analytics": "mei@acme.io",
    },
    "Uber": {"engineering": "priya@acme.io", "sales": "raj@acme.io", "analytics": "mei@acme.io"},
    "Spotify": {"engineering": "priya@acme.io", "sales": "raj@acme.io", "analytics": "mei@acme.io"},
    "Zoom": {"engineering": "priya@acme.io", "sales": "raj@acme.io"},
}

CONTRIBUTIONS = [
    (
        "Pizza Hut",
        "priya@acme.io",
        "Their SSO integration drops sessions on token refresh — root cause is our "
        "Analytics Suite session store not honouring their 15-minute Okta lifetime. "
        "Fix is in release 2.4, scheduled 25 Sep. Until then their users get logged "
        "out roughly hourly, which is where most of the recent tickets come from.",
    ),
    (
        "Pizza Hut",
        "raj@acme.io",
        "Renewal proposal went over at a 15% uplift on 2 Sep. Procurement has it but "
        "their CFO wants usage evidence before signing — they believe seats are "
        "underused. No counter-offer yet; I expect them to push for flat pricing.",
    ),
    (
        "Pizza Hut",
        "mei@acme.io",
        "Weekly active seats are down 31% since the March release, and the drop is "
        "concentrated in the reporting module — scheduled reports fell from 140/week "
        "to 40/week. Dashboard views are flat, so this looks like a broken workflow, "
        "not disengagement.",
    ),
    (
        "Uber",
        "priya@acme.io",
        "They hit our API rate limit every night at 02:00 UTC from a batch export "
        "job; we return 429s for about 20 minutes. Raising their tier limit needs a "
        "commercial decision — engineering side is a config change.",
    ),
    (
        "Uber",
        "raj@acme.io",
        "Expansion to 200 additional Analytics Suite seats is verbally agreed with "
        "their ops director; paperwork is waiting on the rate-limit question above, "
        "which they see as a blocker for the larger footprint.",
    ),
    (
        "Uber",
        "mei@acme.io",
        "Adoption is healthy: 92% of licensed seats active monthly, top quartile. "
        "The nightly export accounts for 60% of their API volume.",
    ),
    (
        "Spotify",
        "priya@acme.io",
        "No open engineering issues. Their data residency request (EU-only "
        "processing) is on the roadmap for Q1 and they know it.",
    ),
    (
        "Spotify",
        "raj@acme.io",
        "Champion (VP Insights) left in August; new contact is the Director of "
        "Analytics, who has not used the product herself. Renewal in Q4 will hinge "
        "on rebuilding that relationship.",
    ),
    (
        "Spotify",
        "mei@acme.io",
        "Usage dipped 18% in the four weeks after the champion left, mostly from "
        "her team's dashboards going unopened. Other teams are steady.",
    ),
    (
        "Zoom",
        "priya@acme.io",
        "Migrated them to the new ingestion pipeline on 1 Sep; latency on their "
        "largest workspace dropped from 9s to 2s. No regressions reported.",
    ),
]


class Command(BaseCommand):
    help = "Adds engineering, sales and analytics people, responsibilities and contributions."

    def add_arguments(self, parser):
        parser.add_argument("--org-email", required=True)

    def handle(self, *args, **options):
        try:
            caller = User.objects.get(email=options["org_email"])
        except User.DoesNotExist as exc:
            raise CommandError(f"No user with email {options['org_email']!r}.") from exc
        org = caller.organisation

        for email, function in EXISTING.items():
            User.objects.filter(email=email, organisation=org).update(function=function)

        users = {}
        created = 0
        for person in PEOPLE:
            user = User.objects.filter(email=person["email"]).first()
            if user is None:
                user = User.objects.create_user(
                    email=person["email"],
                    password=PASSWORD,
                    name=person["name"],
                    organisation=org,
                    role=User.Role.CSM,
                    function=person["function"],
                )
                created += 1
            elif user.organisation_id != org.id:
                raise CommandError(f"{person['email']} exists in another organisation.")
            elif user.function != person["function"]:
                user.function = person["function"]
                user.save(update_fields=["function"])
            users[person["email"]] = user

        owners = 0
        for name, by_function in RESPONSIBLE.items():
            customer = Customer.objects.filter(organisation=org, name=name).first()
            if customer is None:
                self.stderr.write(f"  no customer {name!r}; seed_demo_customers first.")
                continue
            for function, email in by_function.items():
                _, was_created = FunctionOwner.objects.update_or_create(
                    customer=customer, function=function, defaults={"user": users[email]}
                )
                owners += was_created

        written = 0
        for name, email, body in CONTRIBUTIONS:
            customer = Customer.objects.filter(organisation=org, name=name).first()
            if customer is None:
                continue
            author = users[email]
            _, was_created = Contribution.objects.get_or_create(
                organisation=org,
                customer=customer,
                author=author,
                body=body,
                defaults={"function": author.function},
            )
            written += was_created

        self.stdout.write(
            self.style.SUCCESS(
                f"{org.name}: created {created} people, {owners} new responsibilities, "
                f"{written} new contributions."
            )
        )
