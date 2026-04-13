from django.apps import AppConfig


class TelegramConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "telegram"

    def ready(self):
        import telegram.signals  # noqa: F401

        # Register Telegram in the channel adapter registry
        from jina_connect.channel_registry import register_channel

        def _telegram_adapter_factory(tenant):
            """Resolve the Telegram adapter for a tenant."""
            from telegram.models import TelegramBotApp

            bot_app = TelegramBotApp.objects.filter(
                tenant=tenant,
                is_active=True,
            ).first()
            if not bot_app:
                raise ValueError(
                    f"Tenant {tenant.pk} has no active Telegram bot configured."
                )
            from telegram.services.message_sender import TelegramMessageSender

            return TelegramMessageSender(bot_app)

        register_channel("TELEGRAM", _telegram_adapter_factory)
