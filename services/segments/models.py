"""Segments: saved, rule-based groups of organisations, accounts or contacts.

A segment stores rules, never members. Membership is computed for whoever
is looking (`evaluate.members_queryset`), so a segment only ever holds
records its viewer may open. The nightly step (`nightly.py`) evaluates each
segment as its owner and records who entered and who left in
`SegmentChange`; `last_members` is what the next run compares against.

Unrelated to `services/customers/segments.py`, the revenue brackets the
metrics call "Size band".
"""

from django.conf import settings
from django.db import models

from services.accounts.models import Organisation

#: How many segments one person may own.
MAX_OWNED = 50
#: How many conditions one segment may hold, counting each inside a group.
MAX_CONDITIONS = 20
#: How many records one segment may pin in, and how many it may keep out.
MAX_PINNED = 500
#: Beyond this many members the nightly step keeps the count but records no
#: entries or exits: the member list would outgrow a row.
MAX_TRACKED_MEMBERS = 100_000


def default_rules():
    return {"match": "all", "conditions": []}


class Segment(models.Model):
    class Kind(models.TextChoices):
        CUSTOMER = "customer", "Organisations"
        ACCOUNT = "account", "Accounts"
        CONTACT = "contact", "Contacts"

    class Sharing(models.TextChoices):
        PRIVATE = "private", "Only me"
        WORKSPACE = "workspace", "Everyone in the workspace"
        PEOPLE = "people", "Chosen teammates"

    organisation = models.ForeignKey(
        Organisation, related_name="segments", on_delete=models.CASCADE
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="segments", on_delete=models.CASCADE
    )
    name = models.CharField(max_length=120)
    description = models.CharField(max_length=500, blank=True, default="")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    rules = models.JSONField(
        default=default_rules,
        help_text="Rule JSON; see services/segments/registry.py.",
    )
    pinned_ids = models.JSONField(
        default=list, blank=True, help_text="Records kept in, whatever the rules say."
    )
    excluded_ids = models.JSONField(
        default=list, blank=True, help_text="Records kept out, whatever the rules say."
    )
    sharing = models.CharField(max_length=16, choices=Sharing.choices, default=Sharing.PRIVATE)
    shared_with = models.ManyToManyField(
        settings.AUTH_USER_MODEL, related_name="shared_segments", blank=True
    )
    alert_on_changes = models.BooleanField(default=False)
    paused = models.BooleanField(
        default=False,
        help_text="Set by the nightly step while the owner is inactive; evaluation stops.",
    )
    last_members = models.JSONField(
        null=True,
        blank=True,
        help_text="Sorted member ids at the last nightly evaluation, as the owner saw them. "
        "Null before the first one, and for a segment too large to track.",
    )
    member_count = models.PositiveIntegerField(null=True, blank=True)
    last_evaluated_on = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]
        indexes = [models.Index(fields=["organisation", "sharing"])]

    def __str__(self):
        # The name is the owner's own words; an audit row carries the id only.
        return f"Segment {self.pk}"


class SegmentChange(models.Model):
    """One record entering or leaving one segment on one day. `reason` holds
    the field keys that changed the match, never their values."""

    class Change(models.TextChoices):
        ENTERED = "entered", "Entered"
        LEFT = "left", "Left"

    segment = models.ForeignKey(Segment, related_name="changes", on_delete=models.CASCADE)
    record_id = models.PositiveBigIntegerField()
    change = models.CharField(max_length=8, choices=Change.choices)
    changed_on = models.DateField()
    reason = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-changed_on", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["segment", "record_id", "changed_on"],
                name="one_segment_change_per_record_per_day",
            )
        ]
        indexes = [models.Index(fields=["segment", "changed_on"])]

    def __str__(self):
        return f"Segment {self.segment_id}: {self.record_id} {self.change} on {self.changed_on}"
