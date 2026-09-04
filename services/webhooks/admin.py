from django.contrib import admin

from .models import WebhookDelivery, WebhookSubscription


@admin.register(WebhookSubscription)
class WebhookSubscriptionAdmin(admin.ModelAdmin):
    list_display = ["url", "event", "organisation", "is_active", "created_at"]
    list_filter = ["event", "is_active"]


@admin.register(WebhookDelivery)
class WebhookDeliveryAdmin(admin.ModelAdmin):
    list_display = ["webhook", "success", "status_code", "sent_at"]
    list_filter = ["success"]
