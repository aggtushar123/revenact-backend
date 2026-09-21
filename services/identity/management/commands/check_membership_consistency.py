"""Prove that memberships and the columns they will replace still agree.

Two representations of "who belongs where" exist at once during the identity
migration, and authorization now reads the newer one. If they ever disagree,
somebody's access silently changes — which is the single highest-severity
failure mode in this whole piece of work.

So it is checked rather than assumed. Run it after any migration, any bulk user
import, and before the phase that drops the columns:

    python manage.py check_membership_consistency

Exits non-zero when anything diverges, so CI or a deploy step can gate on it.
"""

from django.core.management.base import BaseCommand, CommandError

from services.accounts.models import User
from services.identity.context import active_membership, capabilities_for
from services.identity.models import LIVE_MEMBERSHIP_STATUSES, OrganizationMembership


class Command(BaseCommand):
    help = "Check that memberships agree with User.organisation and User.role."

    def add_arguments(self, parser):
        parser.add_argument("--quiet", action="store_true", help="Only report problems.")

    def handle(self, *args, **options):
        problems = []
        checked = 0

        users = User.objects.select_related("organisation", "role").order_by("email")
        for user in users:
            if user.organisation_id is None:
                # Platform staff. They must own no membership: an operator is
                # not a member of a customer's tenant.
                stray = OrganizationMembership.objects.filter(
                    user=user, status__in=LIVE_MEMBERSHIP_STATUSES
                ).count()
                if stray:
                    problems.append(
                        f"{user.email}: no organisation, but {stray} live membership(s)"
                    )
                continue

            checked += 1
            membership = active_membership(user)

            if membership is None:
                problems.append(
                    f"{user.email}: in {user.organisation.name}, but no live membership"
                )
                continue

            if membership.organisation_id != user.organisation_id:
                problems.append(
                    f"{user.email}: membership says {membership.organisation.name}, "
                    f"column says {user.organisation.name}"
                )

            if membership.role_id != user.role_id:
                problems.append(
                    f"{user.email}: membership role {membership.role_id} "
                    f"!= column role {user.role_id}"
                )

            # The question that actually matters: does authorization give the
            # same answer through the membership as it would from the column?
            from_column = list((user.role.permissions if user.role_id else None) or [])
            from_membership = capabilities_for(user)
            if sorted(from_column) != sorted(from_membership):
                problems.append(
                    f"{user.email}: capabilities differ — column {sorted(from_column)} "
                    f"vs membership {sorted(from_membership)}"
                )

        if problems:
            for problem in problems:
                self.stderr.write(self.style.ERROR(f"  {problem}"))
            raise CommandError(f"{len(problems)} inconsistency(ies) across {checked} member(s)")

        if not options["quiet"]:
            self.stdout.write(
                self.style.SUCCESS(
                    f"{checked} member(s) consistent: membership, column and capabilities agree."
                )
            )
