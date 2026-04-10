"""
Location Message Send Request Validator for META Direct API

This module provides Pydantic validators for META's WhatsApp Business API
location message sending requests during the 24-hour session window.
"""

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class LocationContent(BaseModel):
    """Location content for the message"""
    latitude: float = Field(
        ...,
        ge=-90,
        le=90,
        description="Latitude of the location (-90 to 90)",
    )
    longitude: float = Field(
        ...,
        ge=-180,
        le=180,
        description="Longitude of the location (-180 to 180)",
    )
    name: Optional[str] = Field(
        None,
        max_length=1000,
        description="Name of the location",
    )
    address: Optional[str] = Field(
        None,
        max_length=1000,
        description="Address of the location",
    )


class ContextInfo(BaseModel):
    """Context for replying to a specific message"""
    message_id: str = Field(..., description="The message ID to reply to")


class LocationMessageSendRequestValidator(BaseModel):
    """
    Validator for META Direct API location message send request.

    Location messages display a map with a pin at the specified coordinates.

    Example usage:
        >>> data = {
        ...     "messaging_product": "whatsapp",
        ...     "recipient_type": "individual",
        ...     "to": "919876543210",
        ...     "type": "location",
        ...     "location": {
        ...         "latitude": 37.7749,
        ...         "longitude": -122.4194,
        ...         "name": "San Francisco",
        ...         "address": "San Francisco, CA, USA"
        ...     }
        ... }
        >>> request = LocationMessageSendRequestValidator(**data)
    """

    messaging_product: Literal["whatsapp"] = Field(
        "whatsapp", description="Messaging product (must be 'whatsapp')"
    )
    recipient_type: Literal["individual"] = Field(
        "individual", description="Recipient type (must be 'individual')"
    )
    to: str = Field(..., description="Recipient phone number")
    type: Literal["location"] = Field(
        "location", description="Message type (must be 'location')"
    )
    location: LocationContent = Field(..., description="Location message content")
    context: Optional[ContextInfo] = Field(
        None, description="Context for replying to a specific message"
    )

    @field_validator("to")
    @classmethod
    def validate_phone_number(cls, v):
        if not v or not v.strip():
            raise ValueError("Recipient phone number cannot be empty")
        cleaned = re.sub(r"[^\d+]", "", v)
        if not re.match(r"^\+?[0-9]{10,15}$", cleaned):
            raise ValueError(
                "Invalid phone number format. Must be 10-15 digits, optionally starting with +"
            )
        return cleaned

    def to_meta_payload(self) -> dict:
        """Convert validated request to META API payload format"""
        payload = {
            "messaging_product": self.messaging_product,
            "recipient_type": self.recipient_type,
            "to": self.to,
            "type": self.type,
            "location": self.location.model_dump(exclude_none=True),
        }
        if self.context:
            payload["context"] = self.context.model_dump(exclude_none=True)
        return payload

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": "919876543210",
                    "type": "location",
                    "location": {
                        "latitude": 28.6139,
                        "longitude": 77.2090,
                        "name": "India Gate",
                        "address": "Rajpath, New Delhi, India",
                    },
                },
                {
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": "919876543210",
                    "type": "location",
                    "location": {
                        "latitude": 40.7128,
                        "longitude": -74.0060,
                        "name": "Our Store",
                        "address": "123 Main St, New York, NY 10001",
                    },
                },
            ]
        }
    }


def validate_location_message_send(data: dict) -> LocationMessageSendRequestValidator:
    """Validate a location message send request dictionary."""
    return LocationMessageSendRequestValidator(**data)
