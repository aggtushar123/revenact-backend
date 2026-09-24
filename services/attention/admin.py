from django.contrib import admin

from .models import AttentionSnooze


@admin.register(AttentionSnooze)
class AttentionSnoozeAdmin(admin.ModelAdmin):
    list_display = ("user", "key", "organisation", "until", "created_at")
    list_filter = ("until", "created_at")
    search_fields = ("user__email", "key")
