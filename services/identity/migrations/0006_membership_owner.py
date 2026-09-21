"""Every organisation gets an owner: the person it ultimately belongs to.

Backfill picks, per organisation, the earliest-joined active member holding
the built-in admin role, falling back to the earliest active member. That is
who signed up in every path this application has had, so it is the truth
rather than a guess. Reversible: the flag is cleared.
"""

from django.db import migrations, models


def backfill_owners(apps, schema_editor):
    Organisation = apps.get_model("accounts", "Organisation")
    Membership = apps.get_model("identity", "OrganizationMembership")

    for organisation in Organisation.objects.all().iterator():
        if Membership.objects.filter(organisation=organisation, is_owner=True).exists():
            continue
        active = Membership.objects.filter(
            organisation=organisation, status="active"
        ).select_related("user", "role")
        chosen = (
            active.filter(role__slug="admin").order_by("user__date_joined", "user_id").first()
            or active.order_by("user__date_joined", "user_id").first()
        )
        if chosen is not None:
            chosen.is_owner = True
            chosen.save(update_fields=["is_owner"])


def clear_owners(apps, schema_editor):
    apps.get_model("identity", "OrganizationMembership").objects.update(is_owner=False)


class Migration(migrations.Migration):
    dependencies = [
        ("identity", "0005_invitation"),
        ("accounts", "0013_totp_device"),
    ]

    operations = [
        migrations.AddField(
            model_name="organizationmembership",
            name="is_owner",
            field=models.BooleanField(default=False),
        ),
        migrations.AddConstraint(
            model_name="organizationmembership",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_owner", True)),
                fields=("organisation",),
                name="one_owner_per_organisation",
            ),
        ),
        migrations.RunPython(backfill_owners, clear_owners),
    ]
