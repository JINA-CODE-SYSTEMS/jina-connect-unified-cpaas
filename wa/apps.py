from django.apps import AppConfig


class WaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "wa"

    def ready(self):
        import wa.signals  # noqa: F401

        # Register WhatsApp in the channel adapter registry
        from jina_connect.channel_registry import register_channel

        def _wa_adapter_factory(tenant):
            """Resolve the WhatsApp BSP adapter for a tenant."""
            from wa.adapters import get_bsp_adapter

            wa_app = tenant.wa_apps.first()
            if not wa_app:
                raise ValueError(
                    f"Tenant {tenant.pk} has no WhatsApp app configured."
                )
            return get_bsp_adapter(wa_app)

        register_channel("WHATSAPP", _wa_adapter_factory)
