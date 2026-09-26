from django.db import migrations


def forwards(apps, schema_editor):
    from services.copilot.legacy import mark_legacy_followups

    mark_legacy_followups(apps.get_model("copilot", "Message"))


class Migration(migrations.Migration):
    """Data only: mark plain replies written after an Ask reply, before the
    follow-up snapshot rule, so slice readers are withheld from them
    (services/copilot/legacy.py). The reverse is a no-op."""

    dependencies = [("copilot", "0014_ask_followup_snapshot_help")]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
