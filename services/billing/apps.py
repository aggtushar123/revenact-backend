from django.apps import AppConfig


class BillingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "services.billing"
    label = "billing"

    def ready(self):
        from . import signals  # noqa: F401 — connects the receivers
