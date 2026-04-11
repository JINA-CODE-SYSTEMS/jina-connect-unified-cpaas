from django.core.exceptions import ValidationError


def validate_message_content(value):
    """
    Validate message content structure for team inbox rendering.

    Supported message types:
    - text: Simple text message with optional header, footer, buttons
    - image: Image with optional caption, header, footer, buttons
    - video: Video with optional caption, header, footer, buttons
    - audio: Audio file
    - document: Document file with optional caption, filename
    - cards: Carousel with 1-10 cards, each having image/video and 1-2 buttons

    Structure:
    {
        "type": "text" | "image" | "video" | "audio" | "document" | "cards",
        "header": {"text": "..."} | {"image": {...}} | {"video": {...}} | {"document": {...}},  # optional
        "body": {"text": "..."},  # required for text, optional for others
        "footer": {"text": "..."},  # optional
        "buttons": [...],  # optional, max 3 for regular messages

        # For media types (url required for team inbox rendering):
        "image": {"url": "...", "caption": "..."},
        "video": {"url": "...", "caption": "..."},
        "audio": {"url": "..."},
        "document": {"url": "...", "filename": "...", "caption": "..."},

        # For cards (image or video directly on card, no header):
        "cards": [
            {
                "image": {"url": "..."} | "video": {"url": "..."},  # one required
                "body": {"text": "..."},  # optional
                "buttons": [...]  # required, 1-2 buttons per card
            }
        ]  # 1-10 cards
    }
    """
    if not isinstance(value, dict):
        raise ValidationError("Message content must be a dictionary.")

    # Check for reply message (references existing message)
    if "msg_id" in value:
        if len(value.keys()) != 1:
            raise ValidationError("When using msg_id, no other fields may be included.")
        if not isinstance(value["msg_id"], (str, int)):
            raise ValidationError("msg_id must be a string or integer.")
        return

    # Validate message type
    if "type" not in value:
        raise ValidationError("Message must contain 'type' field.")

    msg_type = value["type"]
    valid_types = {"text", "image", "video", "audio", "document", "cards"}
    if msg_type not in valid_types:
        raise ValidationError(f"Invalid message type: {msg_type}. Must be one of {valid_types}")

    # Dispatch to type-specific validators
    validators = {
        "text": _validate_text_message,
        "image": _validate_image_message,
        "video": _validate_video_message,
        "audio": _validate_audio_message,
        "document": _validate_document_message,
        "cards": _validate_cards_message,
    }

    validators[msg_type](value)


def _validate_header(header):
    """Validate header structure - can be text, image, video, or document."""
    if not isinstance(header, dict):
        raise ValidationError("header must be a dictionary.")

    header_types = {"text", "image", "video", "document"}
    keys = set(header.keys())

    if len(keys) != 1:
        raise ValidationError(f"header must have exactly one of: {header_types}")

    header_type = list(keys)[0]
    if header_type not in header_types:
        raise ValidationError(f"Invalid header type: {header_type}. Must be one of {header_types}")

    if header_type == "text":
        if not isinstance(header["text"], str):
            raise ValidationError("header.text must be a string.")
    else:
        _validate_media_object(header[header_type], header_type)


def _validate_body(body):
    """Validate body structure."""
    if not isinstance(body, dict):
        raise ValidationError("body must be a dictionary.")
    if "text" not in body:
        raise ValidationError("body must contain 'text' field.")
    if not isinstance(body["text"], str):
        raise ValidationError("body.text must be a string.")


def _validate_footer(footer):
    """Validate footer structure."""
    if not isinstance(footer, dict):
        raise ValidationError("footer must be a dictionary.")
    if "text" not in footer:
        raise ValidationError("footer must contain 'text' field.")
    if not isinstance(footer["text"], str):
        raise ValidationError("footer.text must be a string.")


def _validate_buttons(buttons, max_buttons=3, min_buttons=0):
    """Validate buttons array."""
    if not isinstance(buttons, list):
        raise ValidationError("buttons must be a list.")

    if len(buttons) < min_buttons:
        raise ValidationError(f"At least {min_buttons} button(s) required.")

    if len(buttons) > max_buttons:
        raise ValidationError(f"Maximum {max_buttons} buttons allowed.")

    valid_button_types = {"quick_reply", "url", "call"}

    for i, btn in enumerate(buttons):
        if not isinstance(btn, dict):
            raise ValidationError(f"Button {i + 1} must be a dictionary.")

        if "type" not in btn:
            raise ValidationError(f"Button {i + 1} must have 'type' field.")

        if btn["type"] not in valid_button_types:
            raise ValidationError(f"Button {i + 1} has invalid type. Must be one of {valid_button_types}")

        if "text" not in btn:
            raise ValidationError(f"Button {i + 1} must have 'text' field.")

        if not isinstance(btn["text"], str):
            raise ValidationError(f"Button {i + 1} text must be a string.")

        # Validate type-specific fields
        if btn["type"] == "url" and "url" not in btn:
            raise ValidationError(f"URL button {i + 1} must have 'url' field.")

        if btn["type"] == "call" and "phone" not in btn:
            raise ValidationError(f"Call button {i + 1} must have 'phone' field.")


def _validate_media_object(media, media_type):
    """Validate media object structure (image, video, audio, document)."""
    if not isinstance(media, dict):
        raise ValidationError(f"{media_type} must be a dictionary.")

    # URL is required for team inbox rendering
    if "url" not in media:
        raise ValidationError(f"{media_type} must have 'url' field.")

    if not isinstance(media["url"], str):
        raise ValidationError(f"{media_type}.url must be a string.")

    # Optional caption for image, video, document
    if "caption" in media and not isinstance(media["caption"], str):
        raise ValidationError(f"{media_type}.caption must be a string.")

    # Document-specific: filename
    if media_type == "document" and "filename" in media:
        if not isinstance(media["filename"], str):
            raise ValidationError("document.filename must be a string.")


def _validate_common_fields(value, require_body=False):
    """Validate common optional fields: header, body, footer, buttons."""
    if "header" in value:
        _validate_header(value["header"])

    if "body" in value:
        _validate_body(value["body"])
    elif require_body:
        raise ValidationError("body is required for this message type.")

    if "footer" in value:
        _validate_footer(value["footer"])

    if "buttons" in value:
        _validate_buttons(value["buttons"], max_buttons=3)


def _validate_text_message(value):
    """Validate text message - body is required."""
    _validate_common_fields(value, require_body=True)


def _validate_image_message(value):
    """Validate image message."""
    if "image" not in value:
        raise ValidationError("Image message must contain 'image' field.")

    _validate_media_object(value["image"], "image")
    _validate_common_fields(value, require_body=False)


def _validate_video_message(value):
    """Validate video message."""
    if "video" not in value:
        raise ValidationError("Video message must contain 'video' field.")

    _validate_media_object(value["video"], "video")
    _validate_common_fields(value, require_body=False)


def _validate_audio_message(value):
    """Validate audio message."""
    if "audio" not in value:
        raise ValidationError("Audio message must contain 'audio' field.")

    _validate_media_object(value["audio"], "audio")
    # Audio doesn't support header, footer, buttons


def _validate_document_message(value):
    """Validate document message."""
    if "document" not in value:
        raise ValidationError("Document message must contain 'document' field.")

    _validate_media_object(value["document"], "document")
    _validate_common_fields(value, require_body=False)


def _validate_cards_message(value):
    """
    Validate cards (carousel) message.
    - 1-10 cards
    - Each card has image or video (required), optional body, and 1-2 buttons (required)
    """
    if "cards" not in value:
        raise ValidationError("Cards message must contain 'cards' field.")

    cards = value["cards"]
    if not isinstance(cards, list):
        raise ValidationError("cards must be a list.")

    if len(cards) < 1:
        raise ValidationError("At least 1 card is required.")

    if len(cards) > 10:
        raise ValidationError("Maximum 10 cards allowed.")

    for i, card in enumerate(cards):
        if not isinstance(card, dict):
            raise ValidationError(f"Card {i + 1} must be a dictionary.")

        # Card must have image or video (exactly one)
        has_image = "image" in card
        has_video = "video" in card

        if not has_image and not has_video:
            raise ValidationError(f"Card {i + 1} must have 'image' or 'video' field.")

        if has_image and has_video:
            raise ValidationError(f"Card {i + 1} cannot have both 'image' and 'video'.")

        if has_image:
            _validate_media_object(card["image"], "image")
        else:
            _validate_media_object(card["video"], "video")

        # Optional body
        if "body" in card:
            _validate_body(card["body"])

        # Buttons are required for cards (1-2 buttons)
        if "buttons" not in card:
            raise ValidationError(f"Card {i + 1} must have 'buttons' field.")

        _validate_buttons(card["buttons"], max_buttons=2, min_buttons=1)

    # Optional body for the overall carousel message
    if "body" in value:
        _validate_body(value["body"])
