from django.contrib import admin

from .models import Customer


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "organisation",
        "health_score",
        "lifecycle_stage",
        "owner",
        "renewal_date",
    ]
    list_filter = ["organisation", "lifecycle_stage"]
    search_fields = ["name"]
