from django.contrib import admin

from .models import Translation


@admin.register(Translation)
class TranslationAdmin(admin.ModelAdmin):
    list_display = ("kind", "record_id", "language", "detected_language", "created_at")
    list_filter = ("kind", "language")
