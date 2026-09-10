"""Dev/demo convenience — not part of the product. Seeds Contact rows
under existing demo Customers and Accounts so the Organization Details
page's Contacts tab, the standalone Account page's Contacts tab, and
the global /contacts/list page all have real, per-entity data — run
after seed_demo_customers and seed_demo_accounts.

The first 7 entries in DEMO_CUSTOMER_CONTACTS are field-for-field the
original CONTACTS_DATA mock (react-ts-app/src/components/organizations/
contactsData.ts), before Contact existed — same reasoning as every
other seed_demo_* command this session. The rest are new demo content
covering more of the companies seed_demo_customers actually creates,
plus DEMO_ACCOUNT_CONTACTS for a handful of the accounts
seed_demo_accounts creates, so both the "org-level contact" and
"account-level contact" paths (and the Account Contacts tab's own
parent-fallback-free, single-scope rendering) have real data to show.

`last_contacted_at` is set relative to *now* at seed time (hours_ago/
days_ago below) rather than a frozen string, so ActivityFeed's own
"time ago" formatting (react-ts-app's contacts formatters) stays
accurate no matter when this command runs — matching the model's own
docstring on why this is a real datetime, not the mock's static text.

A few rows also set `backdate_days`, moving their `created_at` further
into the past than "now" (bypassing `auto_now_add`, which only fires
on the row's *first* save) — that's what gives ContactStatsView's
`growth_30d_pct` a real, non-null number to show instead of every
demo contact reading as "created today" and none 30+ days old.

Idempotent: matched by (parent, name, email), so re-running updates
existing rows instead of duplicating them. Silently skips any
customer_name/account_name that doesn't exist yet in the target
organisation.

Usage:
    python manage.py seed_demo_contacts --org-email alice@acme.io
"""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from services.accounts.models import User
from services.customers.models import Account, Contact, Customer

# Org-level contacts — customer_name must match a Customer.name already
# seeded by seed_demo_customers.
DEMO_CUSTOMER_CONTACTS = [
    # Apple Inc x5, Pizza Hut, Kraft Heinz — the original mock's own 7
    # contacts, unchanged.
    {
        "customer_name": "Apple Inc",
        "name": "Sarah Chen",
        "role": "executive_sponsor",
        "email": "sarah.chen@apple.com",
        "phone": "+1 (408) 555-0123",
        "status": "active",
        "sentiment": "positive",
        "hours_ago": 2,
    },
    {
        "customer_name": "Apple Inc",
        "name": "James Wilson",
        "role": "champion",
        "email": "j.wilson@apple.com",
        "phone": "+1 (408) 555-0456",
        "status": "active",
        "sentiment": "positive",
        "days_ago": 1,
    },
    {
        "customer_name": "Apple Inc",
        "name": "Elena Rodriguez",
        "role": "economic_buyer",
        "email": "elena.r@apple.com",
        "phone": "+1 (408) 555-0789",
        "status": "active",
        "sentiment": "neutral",
        "days_ago": 3,
    },
    {
        "customer_name": "Apple Inc",
        "name": "Marcus Thorne",
        "role": "technical_lead",
        "email": "m.thorne@apple.com",
        "phone": "+1 (408) 555-0990",
        "status": "active",
        "sentiment": "positive",
        "days_ago": 5,
    },
    {
        "customer_name": "Apple Inc",
        "name": "Olivia Park",
        "role": "influencer",
        "email": "olivia.p@apple.com",
        "phone": "+1 (408) 555-0111",
        "status": "inactive",
        "sentiment": "negative",
        "days_ago": 14,
    },
    {
        "customer_name": "Pizza Hut",
        "name": "David Miller",
        "role": "decision_maker",
        "email": "d.miller@pizzahut.com",
        "phone": "+1 (972) 555-0101",
        "status": "active",
        "sentiment": "negative",
        "hours_ago": 1,
    },
    {
        "customer_name": "Kraft Heinz",
        "name": "Sophie Turner",
        "role": "finance_manager",
        "email": "s.turner@kraftheinz.com",
        "phone": "+1 (312) 555-0202",
        "status": "active",
        "sentiment": "positive",
        "days_ago": 4,
    },
    # New demo contacts, beyond the original mock's 3 named companies —
    # same reasoning as seed_demo_activities/emails/tasks/notes/tickets/
    # calendar_events.
    {
        "customer_name": "Oracle",
        "name": "Priya Nair",
        "role": "economic_buyer",
        "email": "priya.nair@oracle.com",
        "phone": "+1 (650) 506-7001",
        "status": "active",
        "sentiment": "positive",
        "days_ago": 6,
        "backdate_days": 45,
    },
    {
        "customer_name": "Salesforce",
        "name": "Derek Owens",
        "role": "champion",
        "email": "derek.owens@salesforce.com",
        "phone": "+1 (415) 901-7001",
        "status": "active",
        "sentiment": "positive",
        "days_ago": 2,
    },
    {
        "customer_name": "Stripe",
        "name": "Maya Lindqvist",
        "role": "technical_lead",
        "email": "maya.lindqvist@stripe.com",
        "phone": "+1 (888) 926-2290",
        "status": "active",
        "sentiment": "neutral",
        "days_ago": 8,
        "backdate_days": 60,
    },
    {
        "customer_name": "Uber",
        "name": "Connor Blake",
        "role": "decision_maker",
        "email": "connor.blake@uber.com",
        "phone": "+1 (415) 612-8583",
        "status": "active",
        "sentiment": "negative",
        "hours_ago": 5,
    },
    {
        "customer_name": "WeWork",
        "name": "Isla Fenwick",
        "role": "influencer",
        "email": "isla.fenwick@wework.com",
        "phone": "+1 (646) 389-3923",
        "status": "inactive",
        "sentiment": "negative",
        "days_ago": 20,
    },
]

# Account-level contacts — customer_name/account_name must match an
# Account already seeded by seed_demo_accounts (under that customer).
DEMO_ACCOUNT_CONTACTS = [
    {
        "customer_name": "Apple Inc",
        "account_name": "North America Enterprise",
        "name": "Tim Cook Jr.",
        "role": "executive_sponsor",
        "email": "tim.cookjr@apple.com",
        "phone": "+1 (512) 555-0201",
        "status": "active",
        "sentiment": "positive",
        "days_ago": 1,
        "backdate_days": 40,
    },
    {
        "customer_name": "Apple Inc",
        "account_name": "Apple EMEA",
        "name": "Niamh Doyle",
        "role": "champion",
        "email": "niamh.doyle@apple.com",
        "phone": "+353 21 555 0102",
        "status": "active",
        "sentiment": "positive",
        "days_ago": 3,
    },
    {
        "customer_name": "Kraft Heinz",
        "account_name": "Heinz Europe",
        "name": "Lukas Vermeer",
        "role": "finance_manager",
        "email": "lukas.vermeer@kraftheinz.com",
        "phone": "+31 20 555 0198",
        "status": "active",
        "sentiment": "neutral",
        "days_ago": 7,
    },
    {
        "customer_name": "Hyatt Hotels Corporation",
        "account_name": "Hyatt EMEA & APAC",
        "name": "Farah Haddad",
        "role": "decision_maker",
        "email": "farah.haddad@hyatt.com",
        "phone": "+41 44 555 0188",
        "status": "active",
        "sentiment": "positive",
        "days_ago": 2,
    },
]


def _last_contacted_at(row):
    now = timezone.now()
    if "hours_ago" in row:
        return now - timezone.timedelta(hours=row["hours_ago"])
    return now - timezone.timedelta(days=row["days_ago"])


class Command(BaseCommand):
    help = "Seeds demo Contact rows under existing demo Customers/Accounts."

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
        created, updated, skipped = 0, 0, 0

        for row in DEMO_CUSTOMER_CONTACTS:
            try:
                customer = Customer.objects.get(organisation=org, name=row["customer_name"])
            except Customer.DoesNotExist:
                self.stderr.write(
                    f"  skipping contact — no customer {row['customer_name']!r} in {org.name}."
                )
                skipped += 1
                continue

            contact, was_created = Contact.objects.update_or_create(
                customer=customer,
                name=row["name"],
                email=row["email"],
                defaults={
                    "role": row["role"],
                    "phone": row["phone"],
                    "status": row["status"],
                    "sentiment": row["sentiment"],
                    "last_contacted_at": _last_contacted_at(row),
                },
            )
            if "backdate_days" in row:
                Contact.objects.filter(pk=contact.pk).update(
                    created_at=timezone.now() - timezone.timedelta(days=row["backdate_days"])
                )
            created += was_created
            updated += not was_created

        for row in DEMO_ACCOUNT_CONTACTS:
            try:
                account = (
                    Account.objects.filter(
                        customers__organisation=org,
                        customers__name=row["customer_name"],
                        name=row["account_name"],
                    )
                    .distinct()
                    .get()
                )
            except Account.DoesNotExist:
                self.stderr.write(
                    f"  skipping contact — no account {row['account_name']!r} under "
                    f"{row['customer_name']!r} in {org.name}."
                )
                skipped += 1
                continue

            contact, was_created = Contact.objects.update_or_create(
                account=account,
                name=row["name"],
                email=row["email"],
                defaults={
                    "role": row["role"],
                    "phone": row["phone"],
                    "status": row["status"],
                    "sentiment": row["sentiment"],
                    "last_contacted_at": _last_contacted_at(row),
                },
            )
            if "backdate_days" in row:
                Contact.objects.filter(pk=contact.pk).update(
                    created_at=timezone.now() - timezone.timedelta(days=row["backdate_days"])
                )
            created += was_created
            updated += not was_created

        self.stdout.write(
            self.style.SUCCESS(
                f"{org.name}: created {created}, updated {updated}, skipped {skipped} contact(s)."
            )
        )
