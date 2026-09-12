"""Who wrote each user turn, for the turns written before Message.author
existed: the owner, unless a `redirected` event tags it with another
participant."""

from django.db import migrations


def backfill(apps, schema_editor):
    Message = apps.get_model("copilot", "Message")
    SessionEvent = apps.get_model("copilot", "SessionEvent")
    tagged = {
        e.message_id: e.actor_id
        for e in SessionEvent.objects.filter(kind="redirected", message__isnull=False)
    }
    for message in Message.objects.filter(role="user", author__isnull=True).select_related(
        "conversation"
    ):
        message.author_id = tagged.get(message.id, message.conversation.user_id)
        message.save(update_fields=["author"])


class Migration(migrations.Migration):
    dependencies = [("copilot", "0008_org_chart")]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
