"""
WATI Template Message Data Model

Pydantic models for sending template messages via the WATI API.

WATI's send template message API (POST /api/v1/sendTemplateMessage) expects:
    Query params:
        - whatsappNumber (str): Recipient number with country code
    JSON body:
        - template_name (str): Name of the approved template
        - broadcast_name (str): Broadcast tracking name
        - channel_number (str): Sending channel phone number
        - parameters (list): Parameter values for template variables

Reference: https://docs.wati.io/reference/post_api-v1-sendtemplatemessage
"""

from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class TemplateMessageParameter(BaseModel):
    """
    Parameter for template message variable substitution.

    Maps to the ``parameters`` array in WATI's send template message API.
    """
    name: str = Field(
        ..., description="Parameter name matching the template variable"
    )
    value: str = Field(
        ..., description="Value to substitute for the template variable"
    )


class TemplateMessagePayload(BaseModel):
    """
    Complete payload for sending a template message via WATI.

    Usage:
        payload = TemplateMessagePayload(
            template_name="welcome_msg",
            broadcast_name="jan_2026_welcome",
            channel_number="919876543210",
            parameters=[
                TemplateMessageParameter(name="1", value="John"),
                TemplateMessageParameter(name="2", value="Acme Corp"),
            ],
        )
        api.send_template_message(
            whatsapp_number="919876543210",
            data=payload.to_wati_payload(),
        )
    """
    template_name: str = Field(
        ..., min_length=1,
        description="Name of the approved template to send"
    )
    broadcast_name: str = Field(
        ..., min_length=1,
        description="Name for broadcast tracking/analytics"
    )
    channel_number: str = Field(
        ..., min_length=1,
        description="Sending channel phone number with country code"
    )
    parameters: List[TemplateMessageParameter] = Field(
        default_factory=list,
        description="Parameter values for template variable substitution"
    )

    # Optional fields for v2
    header_media_url: Optional[str] = Field(
        None, description="Media URL for template header (if header is media type)"
    )

    @field_validator("template_name")
    @classmethod
    def validate_template_name(cls, v):
        if not v or not v.strip():
            raise ValueError("Template name cannot be empty")
        return v.strip()

    @field_validator("broadcast_name")
    @classmethod
    def validate_broadcast_name(cls, v):
        if not v or not v.strip():
            raise ValueError("Broadcast name cannot be empty")
        return v.strip()

    def to_wati_payload(self) -> dict:
        """
        Convert to WATI API payload dict.

        Returns:
            dict: JSON-serializable dict for WATI send template message API.
        """
        payload = {
            "template_name": self.template_name,
            "broadcast_name": self.broadcast_name,
            "channel_number": self.channel_number,
            "parameters": [p.model_dump() for p in self.parameters],
        }
        return payload
