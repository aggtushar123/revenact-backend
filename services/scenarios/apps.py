from django.apps import AppConfig


class ScenariosConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "services.scenarios"
    label = "scenarios"

    def ready(self):
        # First use of Django signals in this codebase — see signals.py's
        # own docstring for why On Event scenarios need one at all. The
        # import itself is the side effect: it's what connects the
        # `post_save` receiver, nothing here calls it directly.
        from . import signals  # noqa: F401
