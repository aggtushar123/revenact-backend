from django.contrib import admin

from .models import McpToken


@admin.register(McpToken)
class McpTokenAdmin(admin.ModelAdmin):
    list_display = ("label", "user", "hint", "last_used_at", "revoked_at")
    readonly_fields = ("token_hash", "hint")
