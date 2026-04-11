from django.apps import AppConfig


class TeamInboxConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "team_inbox"

    def ready(self):
        # Import signals to register them
        import team_inbox.signals  # noqa: F401
