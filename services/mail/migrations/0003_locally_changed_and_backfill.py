from django.db import migrations, models


def resync_from_scratch(apps, schema_editor):
    """Mailboxes connected before the personal store existed have a cursor
    past all their mail, so nothing would ever be stored for them. Clearing
    it makes the next pass re-list the provider's window (30 days); filing
    and storing are both idempotent on the provider's id, so nothing is
    duplicated and the filed copies simply get their personal twin."""
    MailboxConnection = apps.get_model("mail", "MailboxConnection")
    MailboxConnection.objects.exclude(sync_cursor="").update(sync_cursor="")


class Migration(migrations.Migration):
    dependencies = [("mail", "0002_mail_message")]

    operations = [
        migrations.AddField(
            model_name="mailmessage",
            name="locally_changed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(resync_from_scratch, migrations.RunPython.noop),
    ]
