from django.contrib import admin

from .models import Connector


@admin.register(Connector)
class ConnectorAdmin(admin.ModelAdmin):
    list_display = ["name", "provider", "organisation", "is_enabled", "created_at"]
    list_filter = ["provider", "is_enabled"]
    search_fields = ["name", "organisation__name"]
    filter_horizontal = ["customers", "accounts"]
    readonly_fields = ["created_at"]
