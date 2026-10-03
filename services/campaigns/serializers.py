from django.db.models import Count, Prefetch
from rest_framework import serializers

from services.customers.models import Contact
from services.customers.scoping import visible_children_q

from .models import Campaign


def visible_contacts(user):
    """The contacts `user` may open — the same rule ContactDetailView uses."""
    # SOC2:AUTH-02 a contact follows its organisation's or account's visibility
    return Contact.objects.filter(visible_children_q(user)).distinct()


def with_recipient_visibility(queryset, user):
    """Campaigns with what the serializer needs to read them twice filtered,
    in a fixed number of queries however many recipients there are: the
    recipients `user` may open (`visible_recipients`) and how many there are
    in all (`recipient_total`)."""
    return queryset.annotate(recipient_total=Count("recipients", distinct=True)).prefetch_related(
        Prefetch("recipients", queryset=visible_contacts(user), to_attr="visible_recipients")
    )


def forget_recipient_visibility(campaign):
    """Drop what `with_recipient_visibility` attached, after a write changed it."""
    for attr in ("visible_recipients", "recipient_total"):
        campaign.__dict__.pop(attr, None)


class CampaignSerializer(serializers.ModelSerializer):
    """`recipients` is read-only here — a real M2M to Contact, but
    writing it takes a list of ids, not nested objects, so that's
    handled in the view layer (`recipient_ids` read straight off raw
    request data, same "not a real serializer field" convention
    Survey/Canvas's own flat create views already use for
    `customer_id`/`account_id`, generalized to a list here).

    Twice filtered: a campaign is org-wide, but `recipients` and
    `send_log` list only the contacts the reader may open (needs
    `request` in the context). The rest are only counted, in
    `hidden_recipients`, and name nobody — a contact the reader can't see
    reads like one that doesn't exist. `sent_count`/`skipped_count` count
    the log lines the reader is shown."""

    status_display = serializers.CharField(source="get_status_display", read_only=True)
    recipients = serializers.SerializerMethodField()
    hidden_recipients = serializers.SerializerMethodField()
    send_log = serializers.SerializerMethodField()
    sent_count = serializers.SerializerMethodField()
    skipped_count = serializers.SerializerMethodField()

    class Meta:
        model = Campaign
        fields = [
            "id",
            "name",
            "subject",
            "body",
            "status",
            "status_display",
            "recipients",
            "hidden_recipients",
            "send_log",
            "sent_count",
            "skipped_count",
            "sent_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["status", "sent_at", "created_at", "updated_at"]

    def _visible(self, obj):
        if not hasattr(obj, "visible_recipients"):
            obj.visible_recipients = list(
                obj.recipients.filter(pk__in=visible_contacts(self.context["request"].user))
            )
        return obj.visible_recipients

    def _visible_log(self, obj):
        # Keyed on who the reader may open today. A recipient deleted since
        # the send is no longer anyone's to read, so its line goes too.
        visible_ids = {c.id for c in self._visible(obj)}
        return [entry for entry in obj.send_log if entry.get("contact_id") in visible_ids]

    def get_recipients(self, obj):
        return [{"id": c.id, "name": c.name, "email": c.email} for c in self._visible(obj)]

    def get_hidden_recipients(self, obj):
        total = getattr(obj, "recipient_total", None)
        if total is None:
            total = obj.recipients.count()
        return total - len(self._visible(obj))

    def get_send_log(self, obj):
        return self._visible_log(obj)

    def get_sent_count(self, obj):
        return sum(1 for entry in self._visible_log(obj) if entry.get("status") == "sent")

    def get_skipped_count(self, obj):
        return sum(1 for entry in self._visible_log(obj) if entry.get("status") == "skipped")
