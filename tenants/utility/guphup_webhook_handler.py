from wa.models import WAWebhookEvent
from wa.tasks import process_webhook_event_task
from wa.views import _classify_cloud_api_event


class GupshupWebhookHandler:
    """
    Handles incoming webhooks from WhatsApp Business API providers (Gupshup, Meta, etc.)
    Stores all webhook events in WAWebhookEvent with appropriate event_type.
    """

    @staticmethod
    def _bsp_from_app(wa_app):
        """Return the BSP string from a wa_app, defaulting to GUPSHUP."""
        if wa_app and hasattr(wa_app, 'bsp') and wa_app.bsp:
            return wa_app.bsp
        return 'GUPSHUP'

    def handle_webhook_message(self, data, wa_app=None):
        """
        Handle incoming message webhooks.

        Runs the classifier to detect status/template payloads that Gupshup
        may route to the 'messages' callback URL.
        """
        try:
            # Classify the payload instead of blindly assuming MESSAGE.
            event_type = _classify_cloud_api_event(data) if isinstance(data, dict) else 'MESSAGE'

            row = WAWebhookEvent.objects.create(
                wa_app=wa_app,
                event_type=event_type,
                bsp=self._bsp_from_app(wa_app),
                payload=data,
            )
            # Trigger async processing
            process_webhook_event_task.delay(str(row.pk))
        except Exception as e:
            print(f"Error saving webhook event (MESSAGE): {str(e)}")

    def handle_webhook_template(self, data, wa_app=None):
        """Handle template status webhooks."""
        try:
            row = WAWebhookEvent.objects.create(
                wa_app=wa_app,
                event_type='TEMPLATE',
                bsp=self._bsp_from_app(wa_app),
                payload=data,
            )
            # Trigger async processing
            process_webhook_event_task.delay(str(row.pk))
        except Exception as e:
            print(f"Error saving webhook event (TEMPLATE): {str(e)}")

    def handle_webhook_billing(self, data, wa_app=None):
        """Handle billing webhooks."""
        try:
            row = WAWebhookEvent.objects.create(
                wa_app=wa_app,
                event_type='BILLING',
                bsp=self._bsp_from_app(wa_app),
                payload=data,
            )
            # No async processing for billing events currently
        except Exception as e:
            print(f"Error saving webhook event (BILLING): {str(e)}")

    def handle_webhook_misc(self, data, wa_app=None):
        """Handle misc webhooks (message status updates: delivered, read, sent, etc.)."""
        try:
            row = WAWebhookEvent.objects.create(
                wa_app=wa_app,
                event_type='STATUS',
                bsp=self._bsp_from_app(wa_app),
                payload=data,
            )
            # Trigger async processing for message status updates
            process_webhook_event_task.delay(str(row.pk))
        except Exception as e:
            print(f"Error saving webhook event (STATUS): {str(e)}")                        