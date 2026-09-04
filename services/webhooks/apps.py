from django.apps import AppConfig


class WebhooksConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "services.webhooks"
    label = "webhooks"

    def ready(self):
        # Connects the post_save receiver — see signals.py's own
        # docstring. Same pattern as services.scenarios' own apps.py.
        from . import signals  # noqa: F401
