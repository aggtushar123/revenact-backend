from django.contrib import admin

from .models import FxRate


@admin.register(FxRate)
class FxRateAdmin(admin.ModelAdmin):
    list_display = ["currency", "rate_to_org_currency", "organisation", "updated_at"]
    list_filter = ["currency"]
