from django.db import models


class Campaign(models.Model):
    """A real bulk email send to a chosen list of real Contacts — same
    "full CRUD + a real, deliberately limited engine" framing as
    scenarios.Scenario/webhooks' own delivery: no task queue exists in
    this codebase, so a send (CampaignSendView) runs synchronously,
    in-request, one recipient at a time, via the same services/email.py
    send_mail plumbing already used for password-reset emails and the
    Scenario builder's own "Send Email" action.

    Its own app rather than living in services.customers: a Campaign's
    audience naturally spans many Customers/Accounts at once (`email
    every Champion across the book of business`), so it doesn't fit the
    "belongs to exactly one of Customer or Account" shape Opportunity/
    Risk/Survey/Canvas all share. It's organisation-flat instead, same
    shape as Scenario.

    No `scheduled` status — no task queue exists to honor a future send
    time, the same limit Scenario's own "Schedule" trigger already
    accepts as a frontend-only mockup. Once `status` is SENT, the
    campaign is locked (see CampaignDetailView.perform_update) — you
    can't unsend a real email, so it shouldn't look editable.

    `recipients` is a real ManyToManyField to the existing Contact
    model — no new audience-builder here, just the same real Contacts
    already listed on the standalone /contacts/list page. `send_log` is
    written once, by CampaignSendView, and never touched again — same
    "log list of outcomes" shape as ScenarioRun.log, one entry per
    recipient: `{contact_id, contact_name, status: "sent"|"skipped",
    detail}`. sent_count/skipped_count are derived from this in the
    serializer rather than stored separately, so they can never drift
    from the log itself."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SENT = "sent", "Sent"

    organisation = models.ForeignKey(
        "accounts.Organisation", related_name="campaigns", on_delete=models.CASCADE
    )
    name = models.CharField(max_length=255, default="Untitled Campaign")
    subject = models.CharField(max_length=255, blank=True)
    body = models.TextField(blank=True)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.DRAFT)
    recipients = models.ManyToManyField("customers.Contact", related_name="campaigns", blank=True)
    send_log = models.JSONField(default=list, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.name
