"""Domains become claims: several organisations may hold a pending claim on
one domain, only one may hold it verified.

Needed by self-serve workspace creation: the first person from an unclaimed
corporate domain attaches it, unverified, to the workspace they create. The
company itself must still be able to claim and *verify* the same domain later,
which the old global uniqueness made impossible.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("identity", "0003_accessrequest"),
    ]

    operations = [
        migrations.AlterField(
            model_name="organizationdomain",
            name="domain",
            field=models.CharField(db_index=True, max_length=253),
        ),
        migrations.AddConstraint(
            model_name="organizationdomain",
            constraint=models.UniqueConstraint(
                fields=("organisation", "domain"), name="one_claim_per_organisation_per_domain"
            ),
        ),
        migrations.AddConstraint(
            model_name="organizationdomain",
            constraint=models.UniqueConstraint(
                condition=models.Q(("verification_status", "verified")),
                fields=("domain",),
                name="one_verified_holder_per_domain",
            ),
        ),
    ]
