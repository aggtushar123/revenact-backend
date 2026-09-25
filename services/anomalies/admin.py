from django.contrib import admin

from .models import Anomaly, AnomalyEvidence


@admin.register(Anomaly)
class AnomalyAdmin(admin.ModelAdmin):
    """`title`/`summary` are model-written from customer reports, not
    platform metadata — this is a metadata-only surface, so neither is
    listed, searched, or editable here (`Anomaly.__str__` makes the same
    call for what an audit row records as its target)."""

    list_display = ("id", "organisation", "status", "created_at", "evidence_count")
    list_filter = ("status",)
    exclude = ("title", "summary")

    @admin.display(description="evidence")
    def evidence_count(self, obj):
        return obj.evidence.count()


@admin.register(AnomalyEvidence)
class AnomalyEvidenceAdmin(admin.ModelAdmin):
    list_display = ("kind", "record_id", "anomaly", "customer", "account", "occurred_at")
    list_filter = ("kind",)
