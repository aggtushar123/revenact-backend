from django.apps import AppConfig


class CustomObjectsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "services.custom_objects"
    label = "custom_objects"
