"""One number, for one organisation, as a month ended.

The dashboards compute everything at request time from today's rows, which
answers "how is the book?" and cannot answer "how was it in March?". This is
the history that question needs — one row per metric per period, whole-org
in this first slice, with `dimension`/`member` reserved so a slice by owner
or product later is a row, not a migration.
"""

from django.conf import settings
from django.db import models

from services.accounts.models import Organisation


class MetricSnapshot(models.Model):
    organisation = models.ForeignKey(
        Organisation, related_name="metric_snapshots", on_delete=models.CASCADE
    )
    metric = models.CharField(max_length=64, help_text="A key from services.metrics.registry.")
    dimension = models.CharField(
        max_length=32,
        blank=True,
        help_text='Empty for the whole organisation; later "owner", "product", ...',
    )
    member = models.CharField(
        max_length=64,
        blank=True,
        help_text="The dimension member's id or value; empty for whole-org.",
    )
    period_end = models.DateField(help_text="The last day of the month this row describes.")
    value = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Null when the metric could not be measured that month — not zero.",
    )
    captured_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["metric", "period_end"]
        constraints = [
            models.UniqueConstraint(
                fields=["organisation", "metric", "dimension", "member", "period_end"],
                name="metricsnapshot_one_per_metric_member_period",
            )
        ]
        indexes = [models.Index(fields=["organisation", "metric", "period_end"])]

    def __str__(self):
        return f"{self.metric} @ {self.period_end} = {self.value}"


class Brief(models.Model):
    """One management brief, written by the model from the metric layer.

    Stored rather than regenerated on view: a brief costs a real model call,
    and last week's brief is itself a record — what the figures said then,
    and what was worth watching. `evidence` is the exact data the prompt was
    built from, kept beside the text so any sentence can be checked against
    the numbers it was written from.
    """

    organisation = models.ForeignKey(Organisation, related_name="briefs", on_delete=models.CASCADE)
    as_of = models.DateField(help_text="The day the figures describe.")
    baseline = models.DateField(
        null=True, blank=True, help_text="The month-end the figures were compared against, if any."
    )
    headline = models.CharField(max_length=255)
    body = models.TextField(help_text="Paragraphs separated by blank lines.")
    watch = models.JSONField(default=list, blank=True, help_text="2-4 things to watch next month.")
    evidence = models.JSONField(help_text="What the model was given — the figures, verbatim.")
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    generated_at = models.DateTimeField()

    class Meta:
        ordering = ["-generated_at"]

    def __str__(self):
        return f"Brief for {self.organisation} as of {self.as_of}"


class Initiative(models.Model):
    """A decision, with a number attached.

    Management's half of the brain. The metric layer says what the numbers
    are and what moved them; an initiative says what someone decided to do
    about one of them — a hypothesis, a target, a date, an owner — and is
    then judged against the same registry that everything else reads, so
    "did it work" is a fact rather than a memory.

    `baseline_value` is the starting line, captured when the initiative was
    written: the number as it stood on `baseline_as_of`. Stored rather than
    re-read, because the point is to measure movement since the decision, and
    the registry only knows "now".

    An initiative can target one cut of a metric — ARR at risk on Product B,
    coverage for one owner — through `dimension`/`member`, which must be a
    cut the registry actually has. `member_label` is kept as it read when the
    initiative was written, so the card still says "Product B" if the cut is
    later empty.
    """

    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        ACTIVE = "active", "Active"
        DONE = "done", "Done"
        ABANDONED = "abandoned", "Abandoned"

    organisation = models.ForeignKey(
        Organisation, related_name="initiatives", on_delete=models.CASCADE
    )
    title = models.CharField(max_length=200)
    hypothesis = models.TextField(
        blank=True,
        help_text='"If we …, then … because …". The reasoning, so the outcome can be '
        "judged against it.",
    )
    metric = models.CharField(max_length=64, help_text="A key from services.metrics.registry.")
    dimension = models.CharField(max_length=32, blank=True)
    member = models.CharField(max_length=64, blank=True)
    member_label = models.CharField(max_length=255, blank=True)
    target_value = models.DecimalField(max_digits=18, decimal_places=4)
    target_by = models.DateField()
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="owned_initiatives",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    outcome = models.TextField(blank=True, help_text="What happened, written when it is closed.")
    baseline_value = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    baseline_as_of = models.DateField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title
