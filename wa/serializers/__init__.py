"""
WhatsApp Serializers Package

This package contains all serializers for WhatsApp-related models,
organized into separate modules for better maintainability.

Structure:
    wa/serializers/
    ├── __init__.py           # This file - exports all serializers
    ├── fields.py             # Custom serializer fields
    ├── contacts.py           # WAContacts serializers
    ├── broadcast.py          # WABroadcast serializers
    ├── template.py           # WATemplate serializers (legacy)
    ├── subscription.py       # Subscription serializers
    ├── outgoing_messages.py  # Outgoing message serializers
    ├── request_response.py   # Request/Response utility serializers
    ├── wa_app.py             # WAApp serializers
    ├── wa_template.py        # WATemplate v2 serializers
    ├── wa_message.py         # WAMessage serializers
    ├── wa_webhook_event.py   # WAWebhookEvent serializers
    └── wa_subscription.py    # WASubscription v2 serializers
"""

from wa.serializers.broadcast import WABroadcastSerializer

# Core Serializers
from wa.serializers.contacts import WAContactsSerializer

# Custom Fields
from wa.serializers.fields import TemplateButtonsField, TemplateCardsField
from wa.serializers.outgoing_messages import OutgoingMessagesSerializer

# Rate Card Serializers
from wa.serializers.rate_card import (
    MetaBaseRateSerializer,
    RateCardMarginSerializer,
    TenantRateCardSerializer,
    TenantRateCardSummarySerializer,
)
from wa.serializers.request_response import (
    ChargeBreakdownRequestSerializer,
    ChargeBreakdownStatusSerializer,
    DateTimeRequestSerializer,
    PaginationRequestSerializer,
    SearchRequestSerializer,
)
from wa.serializers.subscription import SubscriptionSerializer
from wa.serializers.template import WATemplateSerializer

# WAApp Serializers
from wa.serializers.wa_app import WAAppCreateSerializer, WAAppListSerializer, WAAppSafeSerializer, WAAppSerializer

# WAMessage Serializers
from wa.serializers.wa_message import WAMessageCreateSerializer, WAMessageListSerializer, WAMessageSerializer

# WASubscription V2 Serializers
from wa.serializers.wa_subscription import WASubscriptionV2ListSerializer, WASubscriptionV2Serializer

# WATemplate V2 Serializers
from wa.serializers.wa_template import WATemplateV2ListSerializer, WATemplateV2Serializer

# WAWebhookEvent Serializers
from wa.serializers.wa_webhook_event import WAWebhookEventListSerializer, WAWebhookEventSerializer

__all__ = [
    # Custom Fields
    "TemplateButtonsField",
    "TemplateCardsField",
    # Core Serializers
    "WAContactsSerializer",
    "WABroadcastSerializer",
    "WATemplateSerializer",
    "SubscriptionSerializer",
    "OutgoingMessagesSerializer",
    "DateTimeRequestSerializer",
    "ChargeBreakdownRequestSerializer",
    "ChargeBreakdownStatusSerializer",
    "PaginationRequestSerializer",
    "SearchRequestSerializer",
    # WAApp Serializers
    "WAAppSerializer",
    "WAAppListSerializer",
    "WAAppSafeSerializer",
    "WAAppCreateSerializer",
    # WATemplate Serializers
    "WATemplateV2Serializer",
    "WATemplateV2ListSerializer",
    # WAMessage Serializers
    "WAMessageSerializer",
    "WAMessageListSerializer",
    "WAMessageCreateSerializer",
    # WAWebhookEvent Serializers
    "WAWebhookEventSerializer",
    "WAWebhookEventListSerializer",
    # WASubscription Serializers
    "WASubscriptionV2Serializer",
    "WASubscriptionV2ListSerializer",
    # Rate Card Serializers
    "TenantRateCardSerializer",
    "TenantRateCardSummarySerializer",
    "MetaBaseRateSerializer",
    "RateCardMarginSerializer",
]
