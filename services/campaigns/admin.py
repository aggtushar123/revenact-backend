from django.contrib import admin

from .models import Campaign


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = ["name", "organisation", "status", "recipient_count", "sent_at"]
    list_filter = ["status"]
    search_fields = ["name"]

    def recipient_count(self, obj):
        return obj.recipients.count()
