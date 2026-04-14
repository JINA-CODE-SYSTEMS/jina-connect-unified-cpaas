from django.apps import AppConfig


class SMSConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "sms"

    def ready(self):
        import sms.signals  # noqa: F401
        from jina_connect.channel_registry import register_channel

        def _sms_adapter_factory(tenant):
            from sms.models import SMSApp
            from sms.services.message_sender import SMSMessageSender

            sms_app = SMSApp.objects.filter(tenant=tenant, is_active=True).first()
            if not sms_app:
                raise ValueError(f"Tenant {tenant.pk} has no active SMS app configured.")
            return SMSMessageSender(sms_app)

        register_channel("SMS", _sms_adapter_factory)
