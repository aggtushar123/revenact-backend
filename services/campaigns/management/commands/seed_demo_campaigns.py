"""Dev/demo convenience — not part of the product. Seeds Campaign rows
against real seeded Contacts so /campaigns has real data immediately —
run after seed_demo_contacts.

One Draft campaign (never sent) and one already-Sent campaign against
Apple Inc's own real seeded Contacts, so both states show up on
/campaigns right away. The Sent one does *not* actually call send_mail
during seeding — that would print (or, with real SMTP creds
configured, actually deliver) an email on every reseed, which a demo
fixture has no business doing. Instead it hand-builds the same
`send_log` shape CampaignSendView itself would have produced — "seed
the state, not the side effect," the same reasoning already used for
seed_demo_surveys.py's own historical responded surveys.

Idempotent: matched by (organisation, name), same spirit as
seed_demo_risks's (parent, title) — re-running updates existing rows
instead of duplicating them.

Usage:
    python manage.py seed_demo_campaigns --org-email alice@acme.io
"""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from services.accounts.models import User
from services.campaigns.models import Campaign
from services.customers.models import Contact, Customer


class Command(BaseCommand):
    help = "Seeds demo Campaign rows against real seeded Customers/Contacts."

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

        try:
            apple = Customer.objects.get(organisation=org, name="Apple Inc")
        except Customer.DoesNotExist:
            self.stderr.write(f"  skipping campaigns — no customer 'Apple Inc' in {org.name}.")
            self.stdout.write(
                self.style.SUCCESS(f"{org.name}: created 0, updated 0, skipped 2 campaign(s).")
            )
            return

        contacts = list(Contact.objects.filter(customer=apple).order_by("name"))
        if not contacts:
            self.stderr.write("  skipping campaigns — Apple Inc has no real seeded contacts yet.")
            self.stdout.write(
                self.style.SUCCESS(f"{org.name}: created 0, updated 0, skipped 2 campaign(s).")
            )
            return

        draft, was_created = Campaign.objects.update_or_create(
            organisation=org,
            name="Q4 Renewal Outreach",
            defaults={
                "subject": "Your Q4 renewal — let's talk",
                "body": (
                    "Hi there,\n\nAs we head into Q4, I wanted to check in ahead of your "
                    "upcoming renewal and see how things are going on your end.\n\n"
                    "Best,\nYour Revenact team"
                ),
                "status": Campaign.Status.DRAFT,
            },
        )
        draft.recipients.set(contacts[:3])
        created += was_created
        updated += not was_created

        sent_at = timezone.now() - timezone.timedelta(days=5)
        send_log = [
            {
                "contact_id": contact.id,
                "contact_name": contact.name,
                "status": "sent",
                "detail": f"Emailed {contact.email}",
            }
            for contact in contacts
        ]
        sent, was_created = Campaign.objects.update_or_create(
            organisation=org,
            name="Product Update — September",
            defaults={
                "subject": "What's new this month",
                "body": (
                    "Hi there,\n\nA quick rundown of what shipped this month...\n\n"
                    "Best,\nYour Revenact team"
                ),
                "status": Campaign.Status.SENT,
                "send_log": send_log,
                "sent_at": sent_at,
            },
        )
        sent.recipients.set(contacts)
        created += was_created
        updated += not was_created

        self.stdout.write(
            self.style.SUCCESS(
                f"{org.name}: created {created}, updated {updated}, skipped {skipped} campaign(s)."
            )
        )
