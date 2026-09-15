from django.contrib import admin

from .models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    """Read-only view of the audit trail (SOC2:LOG-02 append-only)."""

    list_display = ("created_at", "action", "outcome", "actor_email", "organisation", "ip")
    list_filter = ("action", "outcome")
    search_fields = ("actor_email", "target_repr", "request_id", "ip")
    readonly_fields = [field.name for field in AuditEvent._meta.fields]
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
