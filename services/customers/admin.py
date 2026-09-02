from django.contrib import admin

from .models import Account, Activity, Customer


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "organisation",
        "health_score",
        "lifecycle_stage",
        "owner",
        "renewal_date",
        "total_contract_value",
    ]
    list_filter = ["organisation", "lifecycle_stage", "ai_pulse_score"]
    search_fields = ["name", "domain"]
    readonly_fields = ["created_at", "updated_at", "created_by", "modified_by"]


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "customer",
        "health_score",
        "lifecycle_stage",
        "owner",
        "renewal_date",
        "arr",
    ]
    list_filter = ["customer__organisation", "lifecycle_stage", "ai_pulse_score"]
    search_fields = ["name", "domain", "customer__name"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(Activity)
class ActivityAdmin(admin.ModelAdmin):
    list_display = ["type", "customer", "account", "occurred_at", "links", "watchers"]
    list_filter = ["type"]
    search_fields = ["customer__name", "account__name"]
    readonly_fields = ["created_at"]
