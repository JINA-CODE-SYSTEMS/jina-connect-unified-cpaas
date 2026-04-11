from typing import Any, Dict, Optional

from pydantic import BaseModel


class TemplateInput(BaseModel):
    """
    Model to extract and flatten webhook data from Gupshup.
    Handles template status updates and category updates.
    """

    # Core Metadata
    gs_app_id: Optional[str] = None
    timestamp: Optional[int] = None

    # Template Status Update Fields (for template approval/rejection webhooks)
    gs_template_id: Optional[str] = None
    event: Optional[str] = None  # APPROVED, REJECTED, FAILED, CATEGORY_UPDATE, etc.
    reason: Optional[str] = None  # Failure/rejection reason from Gupshup

    # Template Category Update Fields (for pricing changes)
    new_category: Optional[str] = None  # MARKETING, UTILITY, AUTHENTICATION
    previous_category: Optional[str] = None
    message_template_id: Optional[int] = None
    message_template_name: Optional[str] = None
    message_template_language: Optional[str] = None
    message_template_category: Optional[str] = None  # Category from status update webhooks

    # Raw payload for debugging
    raw_payload: Optional[Dict[str, Any]] = None

    @classmethod
    def from_webhook_payload(cls, payload: Dict[str, Any]) -> "TemplateInput":
        """
        Factory method to create TemplateInput from Gupshup webhook payload.
        Primarily handles template status update webhooks.
        """
        try:
            # Extract gs_app_id from top level
            gs_app_id = payload.get("gs_app_id")

            # Navigate to the nested structure
            entry = payload.get("entry", [])
            if not entry:
                return cls(gs_app_id=gs_app_id, raw_payload=payload)

            changes = entry[0].get("changes", [])
            if not changes:
                return cls(gs_app_id=gs_app_id, raw_payload=payload)

            change = changes[0]
            field = change.get("field")
            value = change.get("value", {})

            # Handle template status update webhooks
            if field == "message_template_status_update":
                return cls(
                    gs_app_id=gs_app_id,
                    gs_template_id=value.get("gs_template_id"),
                    event=value.get("event"),
                    reason=value.get("reason"),
                    message_template_id=value.get("message_template_id"),
                    message_template_name=value.get("message_template_name"),
                    message_template_language=value.get("message_template_language"),
                    message_template_category=value.get("message_template_category"),
                    timestamp=entry[0].get("time"),
                    raw_payload=payload,
                )

            # Handle template category update webhooks (affects pricing)
            if field == "template_category_update":
                return cls(
                    gs_app_id=gs_app_id,
                    gs_template_id=value.get("gs_template_id"),
                    event="CATEGORY_UPDATE",
                    new_category=value.get("new_category"),
                    previous_category=value.get("previous_category"),
                    message_template_id=value.get("message_template_id"),
                    message_template_name=value.get("message_template_name"),
                    message_template_language=value.get("message_template_language"),
                    timestamp=entry[0].get("time"),
                    raw_payload=payload,
                )

            # For other webhook types, return with basic metadata
            return cls(gs_app_id=gs_app_id, timestamp=entry[0].get("time"), raw_payload=payload)

        except (KeyError, IndexError, TypeError):
            # If parsing fails, return empty object with raw payload
            return cls(raw_payload=payload)
