from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("copilot", "0011_message_reply_to"),
    ]

    operations = [
        migrations.AlterField(
            model_name="conversation",
            name="origin",
            field=models.JSONField(
                blank=True,
                help_text="Where the conversation started: the first Ask message's context without its focus — on the Dashboard {surface, area, view, filters}, on Organizations {surface, view, filters, labels}. Set once, never overwritten. Null for a conversation that never had an Ask message. Ids, filter values and server-built filter labels only, never record text.",
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name="message",
            name="context",
            field=models.JSONField(
                blank=True,
                help_text="User turns asked from an Ask rail (Dashboard or Organizations): the validated context after the focus was intersected with the asker's filtered book (services/copilot/ask.py). Ids, filter values and server-built filter labels only, never record text. Null on every other turn.",
                null=True,
            ),
        ),
    ]
