"""
WATI Data Models

This package contains Pydantic models and data structures for WATI API interactions.

Modules:
- template_input: Template creation input models
- template_message: Template message sending models
- session_message: Session message types
- message_input: Incoming webhook message parsing
- webhook_event: Webhook event parsing and handling
"""

from wa.utility.data_model.wati.message_input import WATIMessageInput
from wa.utility.data_model.wati.session_message import SessionMessageBase, TextSessionMessage
from wa.utility.data_model.wati.template_input import (
    WATITemplateButton,
    WATITemplateCustomParam,
    WATITemplateHeader,
    WATITemplateInput,
)
from wa.utility.data_model.wati.template_message import TemplateMessageParameter, TemplateMessagePayload
from wa.utility.data_model.wati.webhook_event import WATIWebhookEvent

__all__ = [
    # Template Input
    "WATITemplateInput",
    "WATITemplateHeader",
    "WATITemplateButton",
    "WATITemplateCustomParam",
    # Template Message
    "TemplateMessagePayload",
    "TemplateMessageParameter",
    # Session Messages
    "SessionMessageBase",
    "TextSessionMessage",
    # Message Input (incoming)
    "WATIMessageInput",
    # Webhook Events
    "WATIWebhookEvent",
]
