"""One number, for one organisation, as a month ended.

The dashboards compute everything at request time from today's rows, which
answers "how is the book?" and cannot answer "how was it in March?". This is
the history that question needs — one row per metric per period, whole-org
in this first slice, with `dimension`/`member` reserved so a slice by owner
or product later is a row, not a migration.
"""

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
