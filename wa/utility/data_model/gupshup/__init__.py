"""
Gupshup Data Models

This package contains Pydantic models and data structures for Gupshup API interactions.

Modules:
- create_app: App creation data structures
- host_wallet_data: Wallet data models
- message_input: Message input parsing
- partner_token: Partner token data
- session_message_base: Session message types (Text, Image, Video, etc.)
- subscription: Subscription form data
- template_button_input: Template button parsing and validation
- template_card_input: Template card (carousel) parsing
- template_input: Template webhook input parsing
"""

from wa.utility.data_model.gupshup.host_wallet_data import WalletData
from wa.utility.data_model.gupshup.partner_token import PartnerToken
from wa.utility.data_model.gupshup.session_message_base import (
    SessionMessageBase,
    TextMessage,
    TextMessageInput,
)
from wa.utility.data_model.gupshup.subscription import SubscriptionFormData
from wa.utility.data_model.gupshup.template_button_input import (
    ButtonType,
    TemplateButtonsInput,
    parse_template_buttons,
)
from wa.utility.data_model.gupshup.template_card_input import (
    TemplateCardsInput,
    parse_template_cards,
)
from wa.utility.data_model.gupshup.template_input import TemplateInput

__all__ = [
    # Wallet
    "WalletData",
    # Partner
    "PartnerToken",
    # Session Messages
    "SessionMessageBase",
    "TextMessage",
    "TextMessageInput",
    # Subscription
    "SubscriptionFormData",
    # Template Buttons
    "ButtonType",
    "TemplateButtonsInput",
    "parse_template_buttons",
    # Template Cards
    "TemplateCardsInput",
    "parse_template_cards",
    # Template Input
    "TemplateInput",
]
