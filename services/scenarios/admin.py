from django.contrib import admin

from .models import Scenario, ScenarioRun


@admin.register(Scenario)
class ScenarioAdmin(admin.ModelAdmin):
    list_display = ["name", "organisation", "apply_to", "is_active", "updated_at"]
    list_filter = ["apply_to", "is_active"]
    search_fields = ["name"]


@admin.register(ScenarioRun)
class ScenarioRunAdmin(admin.ModelAdmin):
    list_display = ["scenario", "customer", "triggered_by", "status", "started_at"]
    list_filter = ["triggered_by", "status"]
