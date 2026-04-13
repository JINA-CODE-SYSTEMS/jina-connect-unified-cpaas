"""
Canonical platform choices — single source of truth across all apps.

Import from here instead of defining locally in each app.
Each app may define an alias class with a subset of choices for its own needs.
"""
from django.db import models


class PlatformChoices(models.TextChoices):
    WHATSAPP = "WHATSAPP", "WhatsApp"
    TELEGRAM = "TELEGRAM", "Telegram"
    SMS = "SMS", "SMS"
    EMAIL = "EMAIL", "Email"
    VOICE = "VOICE", "Voice"
