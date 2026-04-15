"""RCS constants and status mappings."""

PROVIDER_CHOICES = [
    ("GOOGLE_RBM", "Google RBM"),
    ("META_RCS", "Meta RCS"),
]

STATUS_CHOICES = [
    ("PENDING", "Pending"),
    ("SENT", "Sent"),
    ("DELIVERED", "Delivered"),
    ("READ", "Read"),
    ("FAILED", "Failed"),
    ("REVOKED", "Revoked"),
]

MESSAGE_TYPE_CHOICES = [
    ("TEXT", "Text"),
    ("RICH_CARD", "Rich Card"),
    ("CAROUSEL", "Carousel"),
    ("MEDIA", "Media"),
    ("LOCATION", "Location"),
]

TRAFFIC_TYPE_CHOICES = [
    ("TRANSACTION", "Transactional"),
    ("PROMOTION", "Promotional"),
    ("AUTHENTICATION", "Authentication"),
    ("SERVICEREQUEST", "Service Request"),
]

WEBHOOK_EVENT_TYPES = [
    ("MESSAGE", "Message"),
    ("SUGGESTION_RESPONSE", "Suggestion Response"),
    ("LOCATION", "Location"),
    ("FILE", "File"),
    ("DELIVERED", "Delivered"),
    ("READ", "Read"),
    ("IS_TYPING", "Is Typing"),
    ("UNKNOWN", "Unknown"),
]

# Google RBM error code → our status
GOOGLE_RBM_ERROR_MAP = {
    400: "FAILED",  # Bad Request
    401: "FAILED",  # Unauthorized (invalid credentials)
    403: "FAILED",  # Permission denied / agent not launched
    404: "FAILED",  # User not RCS-capable → trigger SMS fallback
    429: "PENDING",  # Rate limited — retry
    500: "PENDING",  # Server error — retry
}

# Google RBM event type → our event type
EVENT_TYPE_MAP = {
    "text": "MESSAGE",
    "suggestionResponse": "SUGGESTION_RESPONSE",
    "location": "LOCATION",
    "userFile": "FILE",
    "DELIVERED": "DELIVERED",
    "READ": "READ",
    "IS_TYPING": "IS_TYPING",
}
