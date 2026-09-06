from django.contrib import admin

from .models import CustomFieldDefinition, CustomObjectDefinition, CustomObjectRecord


class CustomFieldDefinitionInline(admin.TabularInline):
    model = CustomFieldDefinition
    extra = 0


@admin.register(CustomObjectDefinition)
class CustomObjectDefinitionAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "organisation",
        "applies_to_customer",
        "applies_to_account",
        "created_at",
    ]
    list_filter = ["applies_to_customer", "applies_to_account"]
    search_fields = ["name", "api_name"]
    inlines = [CustomFieldDefinitionInline]


@admin.register(CustomObjectRecord)
class CustomObjectRecordAdmin(admin.ModelAdmin):
    list_display = ["object_definition", "customer", "account", "created_by", "created_at"]
    list_filter = ["object_definition"]
