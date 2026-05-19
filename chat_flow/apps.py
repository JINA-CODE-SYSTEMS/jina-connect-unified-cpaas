from django.apps import AppConfig


class ChatFlowConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "chat_flow"
    verbose_name = "Chat Flow Management"

    def ready(self) -> None:
        # Importing the trigger-types package populates the registry
        # via each module's ``@register_trigger`` decorator. Done here
        # (not at the top of ``chat_flow.triggers``) to avoid hitting
        # the Django app registry before it's ready.
        try:
            import chat_flow.triggers.types  # noqa: F401
        except Exception:  # pragma: no cover — defensive
            import logging

            logging.getLogger(__name__).exception("[chat_flow] failed to load trigger types at boot")
