from django.apps import AppConfig


class CrmConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "crm"
    verbose_name = "CRM connectors"

    def ready(self) -> None:
        try:
            import crm.adapters  # noqa: F401 — populates the connector registry
        except Exception:  # pragma: no cover
            import logging

            logging.getLogger(__name__).exception("[crm] failed to load adapters at boot")
