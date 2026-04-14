"""SMS constants and status mappings."""

PROVIDER_CHOICES = [
    ("TWILIO", "Twilio"),
    ("MSG91", "MSG91"),
    ("FAST2SMS", "Fast2SMS"),
]

WEBHOOK_EVENT_TYPES = [
    ("INBOUND", "Inbound"),
    ("DLR", "Delivery Report"),
    ("UNKNOWN", "Unknown"),
]

OUTBOUND_STATUS_CHOICES = [
    ("PENDING", "Pending"),
    ("QUEUED", "Queued"),
    ("SENT", "Sent"),
    ("DELIVERED", "Delivered"),
    ("FAILED", "Failed"),
    ("UNDELIVERED", "Undelivered"),
]

TWILIO_STATUS_MAP = {
    "accepted": "QUEUED",
    "queued": "QUEUED",
    "sending": "SENT",
    "sent": "SENT",
    "delivered": "DELIVERED",
    "failed": "FAILED",
    "undelivered": "UNDELIVERED",
}
