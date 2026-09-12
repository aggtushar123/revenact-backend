"""Dev/demo convenience — not part of the product. Seeds Connector rows
so the Ticket Overview dashboard's "Tickets By Origin" chart has real
systems to attribute tickets to — run after seed_demo_customers and
seed_demo_accounts, and before seed_demo_tickets, which attaches
tickets to whatever this creates.

Deliberately spread across the three scoping shapes so the rule is
visible in the demo rather than only in the tests:

* **Zendesk** covers a named set of companies.
* **Jira** covers a different, non-overlapping set — the case the
  feature exists for, where two companies are on different systems.
* **Slack** covers nothing explicitly, so it covers the whole
  organisation.
* **Intercom** is scoped to a single Account rather than a whole
  company, showing the finer grain.
* **Zoom** covers the whole organisation too — it's where seeded Calls
  come from (see seed_demo_calls), and a meeting platform that only some
  companies could dial into wouldn't be a realistic demo.

Idempotent: matched by (organisation, provider, name), so re-running
updates the scope instead of duplicating connectors.

Usage:
    python manage.py seed_demo_connectors --org-email alice@acme.io
"""

from django.core.management.base import BaseCommand, CommandError

from services.accounts.models import User
from services.connectors.models import Connector
from services.customers.models import Account, Customer

DEMO_CONNECTORS = [
    {
        "provider": Connector.Provider.ZENDESK,
        "name": "Zendesk",
        "customer_names": [
            "Apple Inc",
            "Pizza Hut",
            "Hyatt Hotels Corporation",
            "Shopify",
            "Spotify",
            "Twilio",
            "Zoom",
        ],
    },
    {
        "provider": Connector.Provider.JIRA,
        "name": "Jira Software",
        "customer_names": [
            "Kraft Heinz",
            "Arista Networks",
            "Oracle",
            "Salesforce",
            "Stripe",
            "Uber",
            "WeWork",
        ],
    },
    {
        # No scope at all — covers every company in the organisation.
        "provider": Connector.Provider.SLACK,
        "name": "Slack",
        "customer_names": [],
    },
    {
        # No scope at all, like Slack above — the recorder for every seeded
        # Call (see seed_demo_calls).
        "provider": Connector.Provider.ZOOM,
        "name": "Zoom",
        "customer_names": [],
    },
    {
        # Account-level scope: one region, not the whole company.
        "provider": Connector.Provider.INTERCOM,
        "name": "Intercom",
        "customer_names": [],
        "account_names": ["Apple EMEA"],
    },
]


class Command(BaseCommand):
    help = "Seeds demo Connector rows for the target organisation."

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
        created, updated = 0, 0

        for row in DEMO_CONNECTORS:
            connector, was_created = Connector.objects.update_or_create(
                organisation=org,
                provider=row["provider"],
                name=row["name"],
                defaults={"is_enabled": True},
            )
            created += was_created
            updated += not was_created

            customers = Customer.objects.filter(
                organisation=org, name__in=row.get("customer_names", [])
            )
            accounts = Account.objects.filter(
                customers__organisation=org, name__in=row.get("account_names", [])
            ).distinct()
            connector.customers.set(customers)
            connector.accounts.set(accounts)

            scope = (
                "organisation-wide"
                if connector.is_organisation_wide
                else f"{customers.count()} customer(s), {accounts.count()} account(s)"
            )
            self.stdout.write(f"  {connector.name}: {scope}")

        self.stdout.write(
            self.style.SUCCESS(f"{org.name}: created {created}, updated {updated} connector(s).")
        )
