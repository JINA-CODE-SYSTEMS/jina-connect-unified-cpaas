from django.apps import AppConfig


class WaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "wa"

    def ready(self):
        import wa.signals  # noqa: F401
