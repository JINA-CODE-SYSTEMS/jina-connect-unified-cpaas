"""
WAMessage Serializers (v2)

Serializers for WhatsApp Messages.
Supports both v2 native format and legacy (gupshup_model + payload) format
for backward compatibility with existing frontend.
"""

import logging

from drf_yasg import openapi
from rest_framework import serializers

from abstract.serializers import BaseSerializer
from contacts.models import TenantContact
from wa.models import WAApp, WAMessage

logger = logging.getLogger(__name__)


class WAMessageListSerializer(BaseSerializer):
    """
    Minimal serializer for message list views.

    Used for efficient list endpoints with only essential fields.
    """

    class Meta:
        model = WAMessage
        fields = [
            "id",
            "direction",
            "message_type",
            "status",
            "text",
            "created_at",
        ]
        swagger_schema_fields = {
            "type": openapi.TYPE_OBJECT,
            "title": "WAMessageList",
            "description": "Minimal WhatsApp Message for list views",
        }


class WAMessageCreateSerializer(BaseSerializer):
    """
    Serializer for creating outbound messages.

    Accepts TWO payload formats:

    1. **v2 native** (flat fields):
       {
         "wa_app": "<uuid>",
         "contact": <int>,
         "message_type": "TEXT",
         "text": "Hello"
       }

    2. **Legacy format** (gupshup_model + payload):
       {
         "gupshup_model": "<wa_app_id string>",
         "payload": {
           "messaging_product": "whatsapp",
           "to": "919876543210",
           "type": "text",
           "text": {"body": "Hello"}
         }
       }

    When legacy format is detected, fields are extracted automatically.
    """

    # Write-only fields — not on the model
    phone = serializers.CharField(
        required=False,
        write_only=True,
        help_text="Recipient phone number — used to resolve contact when contact ID is not provided",
    )

    # Legacy fields — write-only, not on the model
    gupshup_model = serializers.CharField(
        required=False, write_only=True, help_text="(Legacy) WA App ID string — mapped to wa_app"
    )
    payload = serializers.JSONField(
        required=False, write_only=True, help_text="(Legacy) Raw WhatsApp Cloud API payload"
    )
    order_details = serializers.JSONField(
        required=False,
        write_only=True,
        help_text="Order details JSON for ORDER_DETAILS / INTERACTIVE messages (review_and_pay parameters)",
    )
    body_text = serializers.CharField(required=False, write_only=True, help_text="Body text for interactive messages")
    header_text = serializers.CharField(
        required=False, write_only=True, help_text="Header text for interactive messages (optional)"
    )
    footer_text = serializers.CharField(
        required=False, write_only=True, help_text="Footer text for interactive messages (optional)"
    )

    class Meta:
        model = WAMessage
        fields = [
            "wa_app",
            "contact",
            "message_type",
            "text",
            "template",
            "template_params",
            "media_url",
            "media_id",
            "media_caption",
            "media_filename",
            # Write-only fields
            "phone",
            "order_details",
            "body_text",
            "header_text",
            "footer_text",
            # Legacy fields
            "gupshup_model",
            "payload",
        ]
        extra_kwargs = {
            "wa_app": {"required": False, "help_text": "WA App to send message from"},
            "contact": {"required": False, "help_text": "Contact to send message to"},
            "message_type": {"required": False, "help_text": "Type of message (TEXT, TEMPLATE, IMAGE, etc.)"},
            "text": {"required": False, "help_text": "Message text for TEXT type messages"},
            "template": {"required": False, "help_text": "Template ID for TEMPLATE type messages"},
            "template_params": {"help_text": "Parameters for template placeholders"},
        }

    def _is_legacy_format(self, data):
        """Check if request uses the legacy gupshup_model + payload format."""
        return "gupshup_model" in data or "payload" in data

    def _resolve_wa_app(self, gupshup_model_value):
        """
        Resolve a WA App from a gupshup_model value.
        The frontend sends the app_id string (e.g. the Gupshup app ID).
        Try matching by app_id first, then by pk.
        """
        if not gupshup_model_value:
            return None

        value = str(gupshup_model_value).strip()

        # Try by app_id (the Gupshup/BSP app identifier string)
        app = WAApp.objects.filter(app_id=value).first()
        if app:
            return app

        # Try by primary key (UUID)
        try:
            app = WAApp.objects.get(pk=value)
            return app
        except (WAApp.DoesNotExist, ValueError):
            pass

        return None

    def _resolve_contact(self, phone_number, wa_app):
        """
        Resolve a TenantContact from a phone number.
        Normalises leading '+' and tries common formats.
        """
        if not phone_number:
            return None

        phone = str(phone_number).strip().lstrip("+")

        # Build queryset scoped to the same tenant as the wa_app
        qs = TenantContact.objects.all()
        if wa_app and hasattr(wa_app, "tenant_id"):
            qs = qs.filter(tenant_id=wa_app.tenant_id)

        # Try with and without leading '+'
        contact = qs.filter(phone=f"+{phone}").first()
        if not contact:
            contact = qs.filter(phone=phone).first()

        return contact

    def _extract_from_legacy_payload(self, payload):
        """
        Extract structured fields from a legacy WhatsApp Cloud API payload.

        Input format:
        {
          "messaging_product": "whatsapp",
          "to": "919876543210",
          "type": "text",
          "text": {"body": "Hello"}
        }

        Returns dict with: phone, message_type, text, media_id, media_url,
        media_caption, media_filename
        """
        if not payload or not isinstance(payload, dict):
            return {}

        result = {}

        result["phone"] = payload.get("to", "")

        msg_type = payload.get("type", "text").upper()
        result["message_type"] = msg_type

        # Extract content based on type
        type_lower = payload.get("type", "text").lower()

        if type_lower == "text":
            text_obj = payload.get("text", {})
            if isinstance(text_obj, dict):
                result["text"] = text_obj.get("body", "")
            elif isinstance(text_obj, str):
                result["text"] = text_obj

        elif type_lower in ("image", "video", "audio", "document"):
            media_obj = payload.get(type_lower, {})
            if isinstance(media_obj, dict):
                result["media_id"] = media_obj.get("id")
                result["media_url"] = media_obj.get("link")
                result["media_caption"] = media_obj.get("caption")
                result["media_filename"] = media_obj.get("filename")

        return result

    def to_internal_value(self, data):
        """
        Override to transform legacy format into v2 native format
        before standard field validation runs.
        """
        if not self._is_legacy_format(data):
            # v2 native format — resolve contact from phone if not provided
            mutable = data.copy() if hasattr(data, "copy") else dict(data)
            phone = mutable.pop("phone", None)
            if phone and "contact" not in mutable:
                wa_app_val = mutable.get("wa_app")
                wa_app = None
                if wa_app_val:
                    try:
                        wa_app = WAApp.objects.get(pk=wa_app_val)
                    except (WAApp.DoesNotExist, ValueError):
                        pass
                contact = self._resolve_contact(phone, wa_app)
                if contact:
                    mutable["contact"] = contact.pk
            return super().to_internal_value(mutable)

        # --- Legacy format handling ---
        mutable = data.copy() if hasattr(data, "copy") else dict(data)

        gupshup_model_val = mutable.pop("gupshup_model", None)
        payload = mutable.pop("payload", None)

        # Resolve wa_app from gupshup_model
        wa_app = self._resolve_wa_app(gupshup_model_val)
        if wa_app:
            mutable["wa_app"] = str(wa_app.pk)
        else:
            raise serializers.ValidationError(
                {"wa_app": f'Could not resolve WA App from gupshup_model "{gupshup_model_val}"'}
            )

        # Extract structured fields from payload
        extracted = self._extract_from_legacy_payload(payload)

        # Set message_type if not already set
        if "message_type" not in mutable and extracted.get("message_type"):
            mutable["message_type"] = extracted["message_type"]

        # Set text
        if "text" not in mutable and extracted.get("text"):
            mutable["text"] = extracted["text"]

        # Set media fields
        if extracted.get("media_id") and "media_id" not in mutable:
            mutable["media_id"] = extracted["media_id"]
        if extracted.get("media_url") and "media_url" not in mutable:
            mutable["media_url"] = extracted["media_url"]
        if extracted.get("media_caption") and "media_caption" not in mutable:
            mutable["media_caption"] = extracted["media_caption"]
        if extracted.get("media_filename") and "media_filename" not in mutable:
            mutable["media_filename"] = extracted["media_filename"]

        # Resolve contact from phone number
        phone = extracted.get("phone")
        if phone and "contact" not in mutable:
            contact = self._resolve_contact(phone, wa_app)
            if contact:
                mutable["contact"] = contact.pk
            # contact is optional — don't fail if not found

        # Stash raw payload for raw_payload field
        if payload:
            self._legacy_raw_payload = payload

        logger.info(
            "Legacy payload transformed: gupshup_model=%s → wa_app=%s, type=%s",
            gupshup_model_val,
            wa_app.pk,
            mutable.get("message_type"),
        )

        return super().to_internal_value(mutable)

    def validate(self, data):
        """
        Validate message creation data.
        """
        # wa_app is always required (resolved from gupshup_model or direct)
        if not data.get("wa_app"):
            raise serializers.ValidationError(
                {"wa_app": "This field is required. Send as wa_app (UUID) or gupshup_model (legacy)."}
            )

        message_type = data.get("message_type")

        # TEXT messages require text
        if message_type == "TEXT" and not data.get("text"):
            raise serializers.ValidationError({"text": "Text is required for TEXT message type"})

        # TEMPLATE messages require template
        if message_type == "TEMPLATE" and not data.get("template"):
            raise serializers.ValidationError({"template": "Template is required for TEMPLATE message type"})

        # Media messages require media_url or media_id
        media_types = ["IMAGE", "VIDEO", "AUDIO", "DOCUMENT"]
        if message_type in media_types:
            if not data.get("media_url") and not data.get("media_id"):
                raise serializers.ValidationError(
                    {"media_url": f"media_url or media_id is required for {message_type} message type"}
                )

        # INTERACTIVE (ORDER_DETAILS) messages require order_details and body_text
        if message_type == "INTERACTIVE":
            if not data.get("order_details"):
                raise serializers.ValidationError(
                    {"order_details": "order_details JSON is required for INTERACTIVE message type"}
                )
            if not data.get("body_text"):
                raise serializers.ValidationError({"body_text": "body_text is required for INTERACTIVE message type"})

        return data

    def create(self, validated_data):
        """
        Create WAMessage and attach raw_payload for the sending task.
        """
        # Remove non-model write-only fields that leaked through
        validated_data.pop("phone", None)
        validated_data.pop("gupshup_model", None)
        validated_data.pop("payload", None)

        # Build raw_payload BEFORE popping order fields — the payload builder needs them
        raw_payload = getattr(self, "_legacy_raw_payload", None)
        if not raw_payload:
            raw_payload = self._build_raw_payload(validated_data)

        validated_data["raw_payload"] = raw_payload

        # Now pop write-only order fields that aren't on the model
        validated_data.pop("order_details", None)
        validated_data.pop("body_text", None)
        validated_data.pop("header_text", None)
        validated_data.pop("footer_text", None)

        return super().create(validated_data)

    def _build_raw_payload(self, data):
        """
        Build a WhatsApp Cloud API-compatible raw payload from structured fields.
        Used when the request arrives in v2 native format.
        """
        contact = data.get("contact")
        phone = getattr(contact, "phone", "") if contact else ""
        phone_str = str(phone).lstrip("+") if phone else ""

        msg_type = (data.get("message_type") or "TEXT").lower()

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": phone_str,
            "type": msg_type,
        }

        if msg_type == "text":
            payload["text"] = {
                "body": data.get("text", ""),
                "preview_url": False,
            }
        elif msg_type in ("image", "video", "audio", "document"):
            media_obj = {}
            if data.get("media_id"):
                media_obj["id"] = data["media_id"]
            elif data.get("media_url"):
                # Only include link when id is absent — Cloud API rejects both
                media_obj["link"] = str(data["media_url"])
            if data.get("media_caption"):
                media_obj["caption"] = data["media_caption"]
            if msg_type == "document" and data.get("media_filename"):
                media_obj["filename"] = data["media_filename"]
            payload[msg_type] = media_obj
        elif msg_type == "template":
            template_obj = data.get("template")
            if template_obj:
                template_payload = {
                    "name": template_obj.element_name,
                    "language": {"code": template_obj.language_code or "en"},
                }
                components = []
                template_params = data.get("template_params") or {}

                # Resolve named placeholders from placeholder_mapping
                # e.g. {"content": {"1": "first_name"}} maps positional→named
                mapping = getattr(template_obj, "placeholder_mapping", None) or {}
                body_mapping = mapping.get("content", {})

                # Header parameters
                header_params = template_params.get("header", [])
                if header_params:
                    components.append(
                        {
                            "type": "header",
                            "parameters": header_params,
                        }
                    )

                # Body parameters — auto-inject parameter_name for NAMED templates
                body_params = template_params.get("body", [])
                if body_params and body_mapping:
                    for i, param in enumerate(body_params):
                        if "parameter_name" not in param:
                            name = body_mapping.get(str(i + 1))
                            if name:
                                param["parameter_name"] = name
                if body_params:
                    components.append(
                        {
                            "type": "body",
                            "parameters": body_params,
                        }
                    )

                # Button parameters
                button_params = template_params.get("buttons", [])
                for btn_param in button_params:
                    components.append(btn_param)

                if components:
                    template_payload["components"] = components
                payload["template"] = template_payload
        elif msg_type == "interactive":
            order_details_data = data.get("order_details", {})
            body_text = data.get("body_text", "")
            header_text = data.get("header_text")
            footer_text = data.get("footer_text")

            from wa.utility.data_model.gupshup.session_message_base import (
                InteractiveBody,
                InteractiveFooter,
                InteractiveOrderDetailsContent,
                InteractiveOrderDetailsMessage,
                OrderDetailsAction,
                OrderDetailsParameters,
            )

            # Build interactive content
            header = None
            if header_text:
                header = {"type": "text", "text": header_text}
            footer = None
            if footer_text:
                footer = InteractiveFooter(text=footer_text)

            message = InteractiveOrderDetailsMessage(
                to=phone_str,
                interactive=InteractiveOrderDetailsContent(
                    body=InteractiveBody(text=body_text),
                    header=header,
                    footer=footer,
                    action=OrderDetailsAction(parameters=OrderDetailsParameters(**order_details_data)),
                ),
            )
            payload = message.model_dump(by_alias=True, exclude_none=True)

        return payload


class WAMessageSerializer(BaseSerializer):
    """
    Full serializer for WhatsApp Messages.

    Handles message entities including:
    - Message content (text, media, location)
    - Delivery status tracking
    - Template message parameters
    - Error information
    """

    direction_display = serializers.CharField(
        source="get_direction_display", read_only=True, help_text="Human-readable direction (Inbound/Outbound)"
    )
    status_display = serializers.CharField(
        source="get_status_display", read_only=True, help_text="Human-readable status"
    )
    message_type_display = serializers.CharField(
        source="get_message_type_display", read_only=True, help_text="Human-readable message type"
    )
    contact_phone = serializers.CharField(
        source="contact.phone_number", read_only=True, help_text="Phone number of the contact"
    )
    template_name = serializers.CharField(
        source="template.element_name", read_only=True, help_text="Name of the template used (if any)"
    )

    class Meta:
        model = WAMessage
        fields = [
            "id",
            "wa_app",
            "contact",
            "contact_phone",
            "wa_message_id",
            "direction",
            "direction_display",
            "message_type",
            "message_type_display",
            "status",
            "status_display",
            "text",
            "template",
            "template_name",
            "template_params",
            "media_url",
            "media_mime_type",
            "media_caption",
            "media_filename",
            "button_payload",
            "button_text",
            "latitude",
            "longitude",
            "location_name",
            "location_address",
            "error_code",
            "error_message",
            "sent_at",
            "delivered_at",
            "read_at",
            "failed_at",
            "is_billable",
            "cost",
            "conversation_type",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "wa_message_id",
            "sent_at",
            "delivered_at",
            "read_at",
            "failed_at",
            "error_code",
            "error_message",
            "cost",
            "conversation_type",
            "created_at",
            "updated_at",
        ]
        swagger_schema_fields = {
            "type": openapi.TYPE_OBJECT,
            "title": "WAMessage",
            "description": "WhatsApp Message (v2)",
        }
