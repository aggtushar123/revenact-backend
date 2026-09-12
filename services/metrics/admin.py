from django.contrib import admin

from .models import MetricSnapshot


@admin.register(MetricSnapshot)
class MetricSnapshotAdmin(admin.ModelAdmin):
    list_display = ("organisation", "metric", "dimension", "member", "period_end", "value")
    list_filter = ("organisation", "metric")
    ordering = ("-period_end", "metric")
