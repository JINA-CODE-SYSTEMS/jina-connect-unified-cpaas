from typing import Any, Dict, Optional

from pydantic import BaseModel


class MessageInput(BaseModel):
    """
    Model to extract and flatten webhook message data from Gupshup.
    Handles contact details, message content, and metadata.
    """

    # Contact Information
    contact_phone: Optional[str] = None
    contact_name: Optional[str] = None
    contact_profile_name: Optional[str] = None

    # Message Metadata
    message_id: Optional[str] = None
    gs_app_id: Optional[str] = None
    timestamp: Optional[int] = None
    message_type: Optional[str] = None

    # Text Content
    text: Optional[str] = None
    caption: Optional[str] = None

    # Media Content
    mime_type: Optional[str] = None
    image_url: Optional[str] = None
    video_url: Optional[str] = None
    audio_url: Optional[str] = None
    document_url: Optional[str] = None
    file_url: Optional[str] = None
    file_name: Optional[str] = None
    file_size: Optional[int] = None

    # Location Data
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    location_name: Optional[str] = None
    location_address: Optional[str] = None

    # Interactive Message Data (Button/List replies)
    interactive_type: Optional[str] = None  # 'button_reply' or 'list_reply'
    button_id: Optional[str] = None  # Button payload/ID when user clicks a button
    button_title: Optional[str] = None  # Button display text
    list_id: Optional[str] = None  # List item ID when user selects from list
    list_title: Optional[str] = None  # List item title
    list_description: Optional[str] = None  # List item description

    # Additional Metadata
    context_id: Optional[str] = None  # For replies
    forwarded: Optional[bool] = False
    frequently_forwarded: Optional[bool] = False

    # Raw payload for debugging
    raw_payload: Optional[Dict[str, Any]] = None

    @classmethod
    def from_webhook_payload(cls, payload: Dict[str, Any]) -> "MessageInput":
        """
        Factory method to create MessageInput from Gupshup webhook payload.
        Handles the nested structure: entry -> changes -> value -> messages/contacts
        """
        try:
            # Extract gs_app_id from top level
            gs_app_id = payload.get("gs_app_id")

            # Navigate to the nested message structure
            entry = payload.get("entry", [])
            if not entry:
                return cls(gs_app_id=gs_app_id, raw_payload=payload)

            changes = entry[0].get("changes", [])
            if not changes:
                return cls(gs_app_id=gs_app_id, raw_payload=payload)

            value = changes[0].get("value", {})

            # Extract contact information
            contacts = value.get("contacts", [])
            contact_phone = None
            contact_profile_name = None

            if contacts:
                contact = contacts[0]
                contact_phone = contact.get("wa_id")
                profile = contact.get("profile", {})
                contact_profile_name = profile.get("name")

            # Extract message data
            messages = value.get("messages", [])
            if not messages:
                return cls(
                    contact_phone=contact_phone,
                    contact_profile_name=contact_profile_name,
                    gs_app_id=gs_app_id,
                    raw_payload=payload,
                )

            message = messages[0]

            # Extract message metadata
            message_id = message.get("id")
            timestamp = message.get("timestamp")
            if timestamp:
                try:
                    timestamp = int(timestamp)
                except (ValueError, TypeError):
                    timestamp = None
            message_type = message.get("type")
            contact_phone = contact_phone or message.get("from")  # Fallback to message.from

            # Extract message content based on type
            text = None
            caption = None
            mime_type = None
            image_url = None
            video_url = None
            audio_url = None
            document_url = None
            file_url = None
            file_name = None
            file_size = None

            if message_type == "text":
                text_obj = message.get("text", {})
                text = text_obj.get("body")

            elif message_type == "image":
                image_obj = message.get("image", {})
                image_url = image_obj.get("url")
                mime_type = image_obj.get("mime_type")
                caption = image_obj.get("caption")

            elif message_type == "video":
                video_obj = message.get("video", {})
                video_url = video_obj.get("url")
                mime_type = video_obj.get("mime_type")
                caption = video_obj.get("caption")

            elif message_type == "audio":
                audio_obj = message.get("audio", {})
                audio_url = audio_obj.get("url")
                mime_type = audio_obj.get("mime_type")

            elif message_type == "document":
                doc_obj = message.get("document", {})
                document_url = doc_obj.get("url")
                mime_type = doc_obj.get("mime_type")
                file_name = doc_obj.get("filename")

            elif message_type == "location":
                location_obj = message.get("location", {})
                latitude = location_obj.get("latitude")
                longitude = location_obj.get("longitude")
                location_name = location_obj.get("name")
                location_address = location_obj.get("address")

            elif message_type == "interactive":
                # Handle interactive messages (button clicks, list selections)
                interactive_obj = message.get("interactive", {})
                interactive_type = interactive_obj.get("type")

                if interactive_type == "button_reply":
                    # User clicked a quick reply button
                    button_reply = interactive_obj.get("button_reply", {})
                    button_id = button_reply.get("id")  # Button payload/ID
                    button_title = button_reply.get("title")  # Button text
                    # Store button title as text for easy access
                    text = button_title

                elif interactive_type == "list_reply":
                    # User selected from a list
                    list_reply = interactive_obj.get("list_reply", {})
                    list_id = list_reply.get("id")
                    list_title = list_reply.get("title")
                    list_description = list_reply.get("description")
                    # Store list title as text for easy access
                    text = list_title

            elif message_type == "button":
                # Handle template quick reply button responses
                # Format: {"type": "button", "button": {"text": "Good", "payload": "Good"}}
                button_obj = message.get("button", {})
                button_id = button_obj.get("payload")  # Button payload
                button_title = button_obj.get("text")  # Button display text
                # Store button text for easy access
                text = button_title
                # Set interactive_type for consistent handling downstream
                interactive_type = "button_reply"

            # Extract context and forwarding info
            context = message.get("context", {})
            context_id = context.get("id")
            forwarded = context.get("forwarded", False)
            frequently_forwarded = context.get("frequently_forwarded", False)

        except (KeyError, IndexError, TypeError):
            # If parsing fails, return empty object with raw payload
            return cls(raw_payload=payload)

        return cls(
            contact_phone=contact_phone,
            contact_name=None,  # Not available in this webhook format
            contact_profile_name=contact_profile_name,
            message_id=message_id,
            gs_app_id=gs_app_id,
            timestamp=timestamp,
            message_type=message_type,
            text=text,
            caption=caption,
            mime_type=mime_type,
            image_url=image_url,
            video_url=video_url,
            audio_url=audio_url,
            document_url=document_url,
            file_url=file_url,
            file_name=file_name,
            file_size=file_size,
            latitude=latitude if "latitude" in locals() else None,
            longitude=longitude if "longitude" in locals() else None,
            location_name=location_name if "location_name" in locals() else None,
            location_address=location_address if "location_address" in locals() else None,
            interactive_type=interactive_type if "interactive_type" in locals() else None,
            button_id=button_id if "button_id" in locals() else None,
            button_title=button_title if "button_title" in locals() else None,
            list_id=list_id if "list_id" in locals() else None,
            list_title=list_title if "list_title" in locals() else None,
            list_description=list_description if "list_description" in locals() else None,
            context_id=context_id,
            forwarded=forwarded,
            frequently_forwarded=frequently_forwarded,
            raw_payload=payload,
        )
