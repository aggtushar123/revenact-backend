from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["recipient", "actor", "kind", "message", "is_read", "created_at"]
    list_filter = ["kind", "is_read"]
    search_fields = ["message"]
