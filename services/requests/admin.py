from django.contrib import admin

from .models import FeatureRequest, RequestEvidence


@admin.register(FeatureRequest)
class FeatureRequestAdmin(admin.ModelAdmin):
    list_display = ("title", "organisation", "status", "owner", "updated_at")
    list_filter = ("status",)
    search_fields = ("title", "summary")


@admin.register(RequestEvidence)
class RequestEvidenceAdmin(admin.ModelAdmin):
    list_display = (
        "kind",
        "record_id",
        "request",
        "customer",
        "account",
        "dismissed",
        "occurred_at",
    )
    list_filter = ("kind", "dismissed")
