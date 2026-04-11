"""
WATI Session Message Data Models

Pydantic models for session (open-window) messages sent via the WATI API.

Session messages can be sent within 24h of the customer's last message.

Reference: https://docs.wati.io/reference/post_api-v1-sendsessionmessage-whatsappnumber
"""

from typing import Optional

from pydantic import BaseModel, Field, field_validator


class SessionMessageBase(BaseModel):
    """
    Base data model for WATI session messages.
    """

    whatsapp_number: str = Field(..., description="Recipient WhatsApp number with country code (e.g., '85264318721')")
    channel_phone_number: Optional[str] = Field(None, description="Channel phone number with country code")
    reply_context_id: Optional[str] = Field(None, description="WhatsApp message ID (wamid) of the message to reply to")
    local_message_id: Optional[str] = Field(None, description="Unique message identifier for tracking")

    @field_validator("whatsapp_number")
    @classmethod
    def validate_whatsapp_number(cls, v):
        if not v or not v.strip():
            raise ValueError("WhatsApp number cannot be empty")
        # Remove any non-digit characters for validation
        import re

        digits = re.sub(r"\D", "", v)
        if len(digits) < 10 or len(digits) > 15:
            raise ValueError("WhatsApp number must be 10-15 digits with country code")
        return v.strip()


class TextSessionMessage(SessionMessageBase):
    """
    Data model for sending a text session message via WATI.

    Usage:
        msg = TextSessionMessage(
            whatsapp_number="919876543210",
            message_text="Hello! How can I help you?",
        )
    """

    message_text: str = Field(
        ..., min_length=1, max_length=4096, description="Message text to send (max 4096 characters)"
    )

    @field_validator("message_text")
    @classmethod
    def validate_message_text(cls, v):
        if not v or not v.strip():
            raise ValueError("Message text cannot be empty")
        return v.strip()

    def to_wati_params(self) -> dict:
        """
        Convert to WATI API query parameters.

        Returns:
            dict: Query parameters for WATI session message API.
        """
        params = {"messageText": self.message_text}
        if self.reply_context_id:
            params["replyContextId"] = self.reply_context_id
        if self.channel_phone_number:
            params["channelPhoneNumber"] = self.channel_phone_number
        if self.local_message_id:
            params["localMessageId"] = self.local_message_id
        return params


class FileSessionMessage(SessionMessageBase):
    """
    Data model for sending a file via URL in a session message.

    Usage:
        msg = FileSessionMessage(
            whatsapp_number="919876543210",
            file_url="https://example.com/document.pdf",
            file_name="invoice.pdf",
        )
    """

    file_url: str = Field(..., description="Public URL of the file to send")
    file_name: Optional[str] = Field(None, description="Optional filename override for the recipient")

    @field_validator("file_url")
    @classmethod
    def validate_file_url(cls, v):
        if not v or not v.strip():
            raise ValueError("File URL cannot be empty")
        if not v.startswith(("http://", "https://")):
            raise ValueError("File URL must start with http:// or https://")
        return v.strip()

    def to_wati_payload(self) -> dict:
        """
        Convert to WATI API payload.

        Returns:
            dict: JSON payload for WATI session file via URL API.
        """
        payload = {"url": self.file_url}
        if self.file_name:
            payload["fileName"] = self.file_name
        return payload
