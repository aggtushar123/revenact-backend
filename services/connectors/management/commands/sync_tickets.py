"""Pull every connected ticket source. Runs every few minutes from the
scheduler container (see revenact-infra's compose file)."""

from django.core.management.base import BaseCommand

from services.connectors.models import Connector
from services.connectors.providers import PROVIDERS
from services.connectors.sync import sync_connector


class Command(BaseCommand):
    help = "Sync every connected ticket source into the customer records."

    def add_arguments(self, parser):
        parser.add_argument("--connector", type=int, help="Only this connector id.")

    def handle(self, *args, **options):
        connectors = Connector.objects.filter(
            provider__in=list(PROVIDERS), is_enabled=True
        ).exclude(credentials="")
        if options["connector"]:
            connectors = connectors.filter(pk=options["connector"])
        total = {"created": 0, "updated": 0, "unmatched": 0}
        errors = 0
        for connector in connectors.select_related("organisation"):
            counts = sync_connector(connector)
            for key in total:
                total[key] += counts[key]
            if connector.status == Connector.Status.ERROR:
                errors += 1
                self.stderr.write(f"  {connector}: {connector.error}")
        self.stdout.write(
            self.style.SUCCESS(
                f"synced {connectors.count()} connector(s): {total['created']} new, "
                f"{total['updated']} updated, {total['unmatched']} unmatched, "
                f"{errors} need attention"
            )
        )
