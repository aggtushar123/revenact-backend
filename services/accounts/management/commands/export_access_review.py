"""Quarterly access review export (SOC2:AUTH-10).

    python manage.py export_access_review > access-review-$(date +%F).csv

One row per user across every organisation (or one, with --organisation
<slug>): who they are, which role and capabilities they hold, whether they
are active, when they last logged in and when they joined. Hand the file
to whoever reviews access; the reviewer's sign-off is the evidence.
"""

import csv
import sys

from django.core.management.base import BaseCommand

from services.accounts.models import User


class Command(BaseCommand):
    help = "Export users, roles, capabilities and last login as CSV for an access review."

    def add_arguments(self, parser):
        parser.add_argument("--organisation", help="Limit to one organisation slug.")

    def handle(self, *args, **options):
        users = User.objects.select_related("organisation", "role").order_by(
            "organisation__name", "email"
        )
        if options["organisation"]:
            users = users.filter(organisation__slug=options["organisation"])

        writer = csv.writer(sys.stdout)
        writer.writerow(
            [
                "organisation",
                "email",
                "name",
                "role",
                "capabilities",
                "is_active",
                "is_staff",
                "is_superuser",
                "last_login",
                "date_joined",
            ]
        )
        for user in users:
            writer.writerow(
                [
                    user.organisation.name if user.organisation else "",
                    user.email,
                    user.name,
                    user.role.name if user.role else "",
                    ";".join(user.role.permissions) if user.role else "",
                    user.is_active,
                    user.is_staff,
                    user.is_superuser,
                    user.last_login.isoformat() if user.last_login else "",
                    user.date_joined.isoformat() if user.date_joined else "",
                ]
            )
