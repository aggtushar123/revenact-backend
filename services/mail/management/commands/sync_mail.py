"""Pull every connected mailbox. Runs every few minutes from the
scheduler container (see revenact-infra's compose file); `--user <email>`
for one person."""

from django.core.management.base import BaseCommand

from services.mail.models import MailboxConnection
from services.mail.sync import sync_mailbox


class Command(BaseCommand):
    help = "Sync every connected mailbox into the customer records."

    def add_arguments(self, parser):
        parser.add_argument("--user", help="Only this person's mailbox (their login email).")

    def handle(self, *args, **options):
        connections = MailboxConnection.objects.select_related("user", "organisation")
        if options["user"]:
            connections = connections.filter(user__email=options["user"])
        filed, errors = 0, 0
        for connection in connections:
            count = sync_mailbox(connection)
            filed += count
            if connection.status == MailboxConnection.Status.ERROR:
                errors += 1
                self.stderr.write(f"  {connection.address}: {connection.error}")
        self.stdout.write(
            self.style.SUCCESS(
                f"synced {connections.count()} mailbox(es): filed {filed} email(s), "
                f"{errors} need attention"
            )
        )
