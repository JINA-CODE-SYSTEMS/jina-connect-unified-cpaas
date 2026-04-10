"""
WATI Message Input Data Model

Pydantic model to parse incoming messages from WATI webhooks.

WATI delivers webhook events in a different format from Gupshup/META.
This module normalizes the incoming data into a consistent internal structure.

Reference: https://docs.wati.io/reference/message-received
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class WATIMessageInput(BaseModel):
    """
    Model to extract and flatten incoming message data from WATI webhooks.

    WATI webhook payloads for incoming messages typically include:
    - id: Message WAMID
    - waId: Sender's WhatsApp ID
    - text / type: Message content and type
    - timestamp: Message timestamp
    - senderName: Sender's profile name

    This model normalizes these fields for internal processing.
    """
    # Core message fields
    message_id: Optional[str] = Field(None, description="WhatsApp message ID (wamid)")
    wa_id: Optional[str] = Field(None, description="Sender's WhatsApp ID")
    sender_name: Optional[str] = Field(None, description="Sender's profile name")
    timestamp: Optional[str] = Field(None, description="Message timestamp")

    # Content
    message_type: Optional[str] = Field(
        None, description="Message type (text, image, video, audio, document, etc.)"
    )
    text: Optional[str] = Field(None, description="Text content of the message")
    caption: Optional[str] = Field(None, description="Caption for media messages")

    # Media
    media_url: Optional[str] = Field(None, description="URL of the media file")
    media_mime_type: Optional[str] = Field(None, description="MIME type of the media")
    media_file_name: Optional[str] = Field(None, description="Media file name/path")

    # Location
    latitude: Optional[float] = Field(None, description="Latitude for location messages")
    longitude: Optional[float] = Field(None, description="Longitude for location messages")
    location_name: Optional[str] = Field(None, description="Location name")
    location_address: Optional[str] = Field(None, description="Location address")

    # Contact
    contacts: Optional[List[Dict[str, Any]]] = Field(
        None, description="Contact card data for contact messages"
    )

    # Status
    status: Optional[str] = Field(
        None, description="Message status (sent, delivered, read, failed)"
    )
    status_message_id: Optional[str] = Field(
        None, description="Message ID for status updates"
    )

    # Channel info
    channel_phone_number: Optional[str] = Field(
        None, description="Business phone number that received the message"
    )

    # Raw payload for debugging
    raw_payload: Optional[Dict[str, Any]] = Field(None, description="Raw webhook payload")

    @classmethod
    def from_webhook_payload(cls, payload: Dict[str, Any]) -> "WATIMessageInput":
        """
        Factory method to create WATIMessageInput from a WATI webhook payload.

        WATI webhooks send different event types:
        - messages: Incoming messages from customers
        - statuses: Message delivery status updates

        Args:
            payload: Raw webhook payload from WATI.

        Returns:
            WATIMessageInput instance.
        """
        try:
            # WATI may send the data directly or nested
            # Handle the most common patterns

            # Direct message fields
            message_id = payload.get("id") or payload.get("messageId")
            wa_id = payload.get("waId") or payload.get("senderPhoneNumber")
            sender_name = payload.get("senderName") or payload.get("pushName")
            timestamp = payload.get("timestamp") or payload.get("created")
            message_type = payload.get("type") or payload.get("messageType")

            # Text content
            text = payload.get("text") or payload.get("data")

            # Media content
            media_url = payload.get("mediaUrl") or payload.get("data")
            media_mime_type = payload.get("mimeType")
            media_file_name = payload.get("fileName")
            caption = payload.get("caption")

            # Location
            latitude = payload.get("latitude")
            longitude = payload.get("longitude")
            location_name = payload.get("locationName")
            location_address = payload.get("locationAddress")

            # Status updates
            status = payload.get("status") or payload.get("eventType")
            status_message_id = payload.get("statusMessageId")

            # Channel
            channel_phone_number = payload.get("channelPhoneNumber")

            return cls(
                message_id=message_id,
                wa_id=wa_id,
                sender_name=sender_name,
                timestamp=str(timestamp) if timestamp else None,
                message_type=message_type,
                text=text if message_type == "text" else None,
                caption=caption,
                media_url=media_url if message_type in ("image", "video", "audio", "document") else None,
                media_mime_type=media_mime_type,
                media_file_name=media_file_name,
                latitude=float(latitude) if latitude else None,
                longitude=float(longitude) if longitude else None,
                location_name=location_name,
                location_address=location_address,
                contacts=payload.get("contacts"),
                status=status,
                status_message_id=status_message_id,
                channel_phone_number=channel_phone_number,
                raw_payload=payload,
            )

        except (KeyError, TypeError, ValueError):
            # If parsing fails, return minimal object with raw payload
            return cls(raw_payload=payload)

    @property
    def is_text_message(self) -> bool:
        """Check if this is a text message."""
        return self.message_type == "text"

    @property
    def is_media_message(self) -> bool:
        """Check if this is a media message."""
        return self.message_type in ("image", "video", "audio", "document")

    @property
    def is_location_message(self) -> bool:
        """Check if this is a location message."""
        return self.message_type == "location"

    @property
    def is_contact_message(self) -> bool:
        """Check if this is a contact message."""
        return self.message_type == "contacts"

    @property
    def is_status_update(self) -> bool:
        """Check if this is a status update event."""
        return self.status is not None and self.message_type is None
