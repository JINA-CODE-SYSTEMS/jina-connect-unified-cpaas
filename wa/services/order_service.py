"""
Order Service — single entry point for order lifecycle operations.

Created: BE-12 (send_order_status), BE-13 (lookup_payment, initiate_refund)
"""

import logging

import requests
from django.conf import settings
from django.core.exceptions import ValidationError

logger = logging.getLogger(__name__)


class OrderService:
    """Stateless service class for WAOrder operations."""

    # ── BE-12: Send Order Status ──────────────────────────────────────

    @staticmethod
    def send_order_status(
        wa_order,
        new_status: str,
        body_text: str,
        description: str = "",
    ):
        """
        Validate transition → Build order_status payload → Create WAMessage
        → Update WAOrder.

        WAMessage creation triggers post_save signal → Celery → BSP API
        (existing pipeline).

        Returns:
            WAMessage instance that was created.
        """
        from wa.models import MessageDirection, MessageStatus, WAMessage

        # 1. Validate transition
        if not wa_order.can_transition_to(new_status):
            raise ValidationError(f"Cannot transition from '{wa_order.order_status}' to '{new_status}'")
        if new_status == "canceled" and not wa_order.can_cancel():
            raise ValidationError("Cannot cancel: order already has a successful payment")

        # 2. Build order_status Cloud API payload
        phone = getattr(wa_order.contact, "wa_number", "") if wa_order.contact else ""
        phone_str = str(phone).lstrip("+") if phone else ""

        raw_payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": phone_str,
            "type": "interactive",
            "interactive": {
                "type": "order_status",
                "body": {"text": body_text},
                "action": {
                    "name": "review_order",
                    "parameters": {
                        "reference_id": wa_order.reference_id,
                        "order": {
                            "status": new_status,
                            **({"description": description} if description else {}),
                        },
                    },
                },
            },
        }

        # 3. Create WAMessage → triggers send pipeline via post_save signal
        wa_message = WAMessage.objects.create(
            wa_app=wa_order.wa_app,
            contact=wa_order.contact,
            direction=MessageDirection.OUTBOUND,
            status=MessageStatus.PENDING,
            message_type="INTERACTIVE",
            raw_payload=raw_payload,
        )

        # 4. Update WAOrder
        wa_order.order_status = new_status
        wa_order.order_status_messages.add(wa_message)
        wa_order.save(update_fields=["order_status", "updated_at"])

        logger.info(
            "Order %s status updated to %s (WAMessage %s)",
            wa_order.reference_id,
            new_status,
            wa_message.pk,
        )
        return wa_message

    # ── BE-13: Payment Lookup ─────────────────────────────────────────

    @staticmethod
    def lookup_payment(wa_order) -> dict:
        """
        GET /{phone_number_id}/payments/{config}/{reference_id}

        Calls META Cloud API directly to check payment status.
        """
        wa_app = wa_order.wa_app
        token = OrderService._get_access_token(wa_app)

        url = (
            f"https://graph.facebook.com/v21.0/"
            f"{wa_app.phone_number_id}/payments/"
            f"{wa_order.configuration_name}/{wa_order.reference_id}"
        )
        headers = {"Authorization": f"Bearer {token}"}
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()

    # ── BE-13: Initiate Refund ────────────────────────────────────────

    @staticmethod
    def initiate_refund(wa_order, speed: str = "normal") -> dict:
        """
        POST /{phone_number_id}/payments_refund — full refund only for MVP.
        """
        from wa.models import PaymentStatus, WAPaymentEvent

        if wa_order.payment_status != PaymentStatus.CAPTURED:
            raise ValidationError("Can only refund captured payments")

        wa_app = wa_order.wa_app
        token = OrderService._get_access_token(wa_app)

        url = f"https://graph.facebook.com/v21.0/{wa_app.phone_number_id}/payments_refund"
        payload = {
            "reference_id": wa_order.reference_id,
            "speed": speed,
            "payment_config_id": wa_order.configuration_name,
            "amount": {
                "currency": wa_order.currency or "INR",
                "value": str(wa_order.total_amount),
                "offset": "100",
            },
        }
        headers = {"Authorization": f"Bearer {token}"}
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        result = response.json()

        wa_order.payment_status = PaymentStatus.REFUND_PENDING
        wa_order.save(update_fields=["payment_status", "updated_at"])

        WAPaymentEvent.objects.create(
            order=wa_order,
            status="refund_initiated",
            amount_value=wa_order.total_amount,
            currency=wa_order.currency or "INR",
            raw_payload=result,
        )

        logger.info(
            "Refund initiated for order %s (speed=%s)",
            wa_order.reference_id,
            speed,
        )
        return result

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _get_access_token(wa_app) -> str:
        """Resolve META Graph API access token from WAApp credentials."""
        creds = wa_app.bsp_credentials or {}
        token = creds.get("access_token") or getattr(settings, "META_PERM_TOKEN", None)
        if not token:
            raise ValidationError(
                "META access token not configured. Set "
                "bsp_credentials.access_token on the WAApp or "
                "META_PERM_TOKEN in settings."
            )
        return token
