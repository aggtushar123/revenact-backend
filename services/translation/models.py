from django.db import models

from services.accounts.models import Organisation


class Translation(models.Model):
    """One record's words in one other language, kept so the next person to
    open it pays nothing.

    Keyed by the record rather than by the text: the same message read by
    four people is one translation, and a record whose text is edited is a
    different question the cache simply misses (a translation names the
    words it was made from by `source_hash`).

    A translation is a copy of the record's words, so reading one is gated
    on the record itself, every time, by the view. Nothing here is safe to
    hand out on its own."""

    class Kind(models.TextChoices):
        EMAIL = "email", "Email"
        TICKET = "ticket", "Ticket"
        CALL = "call", "Call"
        MAIL_MESSAGE = "mail_message", "Mailbox message"

    organisation = models.ForeignKey(
        Organisation, related_name="translations", on_delete=models.CASCADE
    )
    kind = models.CharField(max_length=16, choices=Kind.choices)
    record_id = models.PositiveIntegerField()
    #: The language asked for, as a short code ("en", "pt-br").
    language = models.CharField(max_length=12)
    #: What the model says the original was written in.
    detected_language = models.CharField(max_length=12, blank=True, default="")
    text = models.TextField()
    #: A digest of the words translated, so an edited record misses the
    #: cache rather than showing a translation of what it used to say.
    source_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["kind", "record_id", "language"],
                name="one_translation_per_record_per_language",
            )
        ]

    def __str__(self):
        return f"{self.kind} {self.record_id} → {self.language}"
