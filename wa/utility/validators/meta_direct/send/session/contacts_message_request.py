"""
Contacts Message Send Request Validator for META Direct API

This module provides Pydantic validators for META's WhatsApp Business API
contacts message sending requests during the 24-hour session window.
"""

import re
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class ContactName(BaseModel):
    """Contact name information"""
    formatted_name: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Full formatted name (required)",
    )
    first_name: Optional[str] = Field(None, max_length=500, description="First name")
    last_name: Optional[str] = Field(None, max_length=500, description="Last name")
    middle_name: Optional[str] = Field(None, max_length=500, description="Middle name")
    suffix: Optional[str] = Field(None, max_length=500, description="Name suffix")
    prefix: Optional[str] = Field(None, max_length=500, description="Name prefix")


class ContactPhone(BaseModel):
    """Contact phone number"""
    phone: str = Field(..., description="Phone number")
    type: Optional[Literal["CELL", "MAIN", "IPHONE", "HOME", "WORK"]] = Field(
        None, description="Phone type"
    )
    wa_id: Optional[str] = Field(None, description="WhatsApp ID")


class ContactEmail(BaseModel):
    """Contact email address"""
    email: str = Field(..., description="Email address")
    type: Optional[Literal["HOME", "WORK"]] = Field(None, description="Email type")


class ContactAddress(BaseModel):
    """Contact address"""
    street: Optional[str] = Field(None, description="Street address")
    city: Optional[str] = Field(None, description="City")
    state: Optional[str] = Field(None, description="State")
    zip: Optional[str] = Field(None, description="ZIP/Postal code")
    country: Optional[str] = Field(None, description="Country")
    country_code: Optional[str] = Field(None, description="Two-letter country code")
    type: Optional[Literal["HOME", "WORK"]] = Field(None, description="Address type")


class ContactOrg(BaseModel):
    """Contact organization"""
    company: Optional[str] = Field(None, description="Company name")
    department: Optional[str] = Field(None, description="Department")
    title: Optional[str] = Field(None, description="Job title")


class ContactUrl(BaseModel):
    """Contact URL"""
    url: str = Field(..., description="URL")
    type: Optional[Literal["HOME", "WORK"]] = Field(None, description="URL type")


class Contact(BaseModel):
    """Single contact information"""
    name: ContactName = Field(..., description="Contact name (required)")
    phones: Optional[List[ContactPhone]] = Field(None, description="Phone numbers")
    emails: Optional[List[ContactEmail]] = Field(None, description="Email addresses")
    addresses: Optional[List[ContactAddress]] = Field(None, description="Addresses")
    org: Optional[ContactOrg] = Field(None, description="Organization")
    urls: Optional[List[ContactUrl]] = Field(None, description="URLs")
    birthday: Optional[str] = Field(
        None,
        description="Birthday in YYYY-MM-DD format",
    )

    @field_validator("birthday")
    @classmethod
    def validate_birthday(cls, v):
        if v:
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", v):
                raise ValueError("Birthday must be in YYYY-MM-DD format")
        return v


class ContextInfo(BaseModel):
    """Context for replying to a specific message"""
    message_id: str = Field(..., description="The message ID to reply to")


class ContactsMessageSendRequestValidator(BaseModel):
    """
    Validator for META Direct API contacts message send request.

    Contact messages allow sharing contact cards with vCard information.

    Example usage:
        >>> data = {
        ...     "messaging_product": "whatsapp",
        ...     "recipient_type": "individual",
        ...     "to": "919876543210",
        ...     "type": "contacts",
        ...     "contacts": [{
        ...         "name": {"formatted_name": "John Doe", "first_name": "John", "last_name": "Doe"},
        ...         "phones": [{"phone": "+1234567890", "type": "WORK"}],
        ...         "emails": [{"email": "john@example.com", "type": "WORK"}]
        ...     }]
        ... }
        >>> request = ContactsMessageSendRequestValidator(**data)
    """

    messaging_product: Literal["whatsapp"] = Field(
        "whatsapp", description="Messaging product (must be 'whatsapp')"
    )
    recipient_type: Literal["individual"] = Field(
        "individual", description="Recipient type (must be 'individual')"
    )
    to: str = Field(..., description="Recipient phone number")
    type: Literal["contacts"] = Field(
        "contacts", description="Message type (must be 'contacts')"
    )
    contacts: List[Contact] = Field(
        ...,
        min_length=1,
        description="List of contacts to share",
    )
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
            "contacts": [c.model_dump(exclude_none=True) for c in self.contacts],
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
                    "type": "contacts",
                    "contacts": [
                        {
                            "name": {
                                "formatted_name": "John Doe",
                                "first_name": "John",
                                "last_name": "Doe",
                            },
                            "phones": [
                                {"phone": "+14155551234", "type": "WORK", "wa_id": "14155551234"}
                            ],
                            "emails": [{"email": "john.doe@example.com", "type": "WORK"}],
                            "org": {
                                "company": "Example Corp",
                                "department": "Sales",
                                "title": "Sales Manager",
                            },
                        }
                    ],
                },
                {
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": "919876543210",
                    "type": "contacts",
                    "contacts": [
                        {
                            "name": {"formatted_name": "Support Team"},
                            "phones": [{"phone": "+18001234567", "type": "MAIN"}],
                            "emails": [{"email": "support@example.com"}],
                            "addresses": [
                                {
                                    "street": "123 Business Ave",
                                    "city": "San Francisco",
                                    "state": "CA",
                                    "zip": "94105",
                                    "country": "United States",
                                    "type": "WORK",
                                }
                            ],
                        }
                    ],
                },
            ]
        }
    }


def validate_contacts_message_send(data: dict) -> ContactsMessageSendRequestValidator:
    """Validate a contacts message send request dictionary."""
    return ContactsMessageSendRequestValidator(**data)
