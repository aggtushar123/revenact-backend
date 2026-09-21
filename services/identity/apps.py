from django.apps import AppConfig


class IdentityConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "services.identity"
    label = "identity"

    def ready(self):
        from . import signals  # noqa: F401  (registers the post_save receiver)
