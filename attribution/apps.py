from django.apps import AppConfig


class AttributionConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "attribution"
    verbose_name = "Attribution (Meta CAPI)"

    def ready(self) -> None:
        # Connect the post_save signal that fires Lead events on
        # qualification transitions. Imported here so Django's app
        # registry is loaded before the receiver runs.
        try:
            import attribution.signals  # noqa: F401
        except Exception:  # pragma: no cover — defensive
            import logging

            logging.getLogger(__name__).exception("[attribution] failed to load signal handlers at boot")
