"""
Telegram Bot API constants — error mapping, event classification, callback format.
"""

# Telegram Bot API error codes → internal status
TELEGRAM_ERROR_MAP = {
    400: "FAILED",   # Bad Request
    401: "FAILED",   # Unauthorized (invalid token)
    403: "BLOCKED",  # Bot blocked by user
    404: "FAILED",   # Chat not found
    429: "PENDING",  # Rate limited — retry
    500: "PENDING",  # Server error — retry
}

# Update type classification (top-level key in Telegram Update object → our label)
UPDATE_TYPE_MAP = {
    "message": "MESSAGE",
    "edited_message": "EDITED_MESSAGE",
    "callback_query": "CALLBACK_QUERY",
    "inline_query": "INLINE_QUERY",
}

# Callback data versioning
CALLBACK_DATA_VERSION = "v1"
CALLBACK_DATA_MAX_LENGTH = 64  # Telegram hard limit (bytes)

# Event type choices for TelegramWebhookEvent.event_type
EVENT_TYPE_CHOICES = [
    ("MESSAGE", "Message"),
    ("CALLBACK_QUERY", "Callback Query"),
    ("EDITED_MESSAGE", "Edited Message"),
    ("INLINE_QUERY", "Inline Query"),
    ("UNKNOWN", "Unknown"),
]

# Outbound message type choices
MESSAGE_TYPE_CHOICES = [
    ("TEXT", "Text"),
    ("PHOTO", "Photo"),
    ("DOCUMENT", "Document"),
    ("VIDEO", "Video"),
    ("AUDIO", "Audio"),
    ("VOICE", "Voice"),
    ("LOCATION", "Location"),
    ("CONTACT", "Contact"),
    ("CALLBACK_ANSWER", "Callback Answer"),
]

# Status choices for outbound messages
OUTBOUND_STATUS_CHOICES = [
    ("PENDING", "Pending"),
    ("SENT", "Sent"),
    ("FAILED", "Failed"),
    ("BLOCKED", "Blocked"),
]
