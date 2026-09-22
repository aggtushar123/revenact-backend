from django.contrib import admin

from .models import AIAttribute, AIAttributeValue


@admin.register(AIAttribute)
class AIAttributeAdmin(admin.ModelAdmin):
    list_display = ("name", "organisation", "value_type", "refresh", "created_at")
    list_filter = ("value_type", "refresh")
    search_fields = ("name", "prompt")


@admin.register(AIAttributeValue)
class AIAttributeValueAdmin(admin.ModelAdmin):
    list_display = ("attribute", "customer", "account", "value", "status", "origin", "computed_at")
    list_filter = ("status", "origin")
    readonly_fields = [f.name for f in AIAttributeValue._meta.fields]
