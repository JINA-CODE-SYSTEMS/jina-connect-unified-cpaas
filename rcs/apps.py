from django.apps import AppConfig


class RCSConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "rcs"

    def ready(self):
        import rcs.signals  # noqa: F401
        from jina_connect.channel_registry import register_channel

        def _rcs_adapter_factory(tenant):
            from rcs.models import RCSApp
            from rcs.services.message_sender import RCSMessageSender

            rcs_app = RCSApp.objects.filter(
                tenant=tenant,
                is_active=True,
            ).first()
            if not rcs_app:
                raise ValueError(f"Tenant {tenant.pk} has no active RCS app configured.")
            return RCSMessageSender(rcs_app)

        register_channel("RCS", _rcs_adapter_factory)
