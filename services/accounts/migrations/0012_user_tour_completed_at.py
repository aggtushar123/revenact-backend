from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0011_organisation_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="tour_completed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
