from django.contrib import admin

from .models import Anomaly, AnomalyEvidence


@admin.register(Anomaly)
class AnomalyAdmin(admin.ModelAdmin):
    list_display = ("title", "organisation", "status", "first_seen_at", "last_seen_at")
    list_filter = ("status",)
    search_fields = ("title", "summary")


@admin.register(AnomalyEvidence)
class AnomalyEvidenceAdmin(admin.ModelAdmin):
    list_display = ("kind", "record_id", "anomaly", "customer", "account", "occurred_at")
    list_filter = ("kind",)
