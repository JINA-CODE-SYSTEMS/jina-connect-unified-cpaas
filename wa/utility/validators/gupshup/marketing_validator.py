
from typing import List, Literal, Optional

from pydantic import field_validator, model_validator
from wa.utility.data_model.gupshup.template_button_input import (
    TemplateButtonsInput, parse_template_buttons)
from wa.utility.data_model.gupshup.template_card_input import (
    TemplateCardsInput, parse_template_cards)

from .base_validatory import BaseTemplateValidator


class MarketingTemplateValidator(BaseTemplateValidator):
    """
    Gupshup MARKETING template validator.

    Validates buttons, cards, media, and enforces mutual exclusivity
    between cards and template-level buttons before submission to Gupshup.
    """
    category: Literal['MARKETING']
    message_send_ttl_seconds: Optional[int] = 1800 # 30 minutes
    buttons: Optional[TemplateButtonsInput] = None # RIGHT NOW WE DO NOT CHECK IF OTP type button IS NOT THERE
    exampleMedia: Optional[str] = None # handle_id by gupshup for media based templates
    cards: Optional[TemplateCardsInput] = None # handle_id by gupshup for card based templates
    
    @field_validator('buttons', mode='before')
    @classmethod
    def validate_buttons(cls, v):
        """
        Convert raw JSON buttons data to TemplateButtonsInput object.
        Handles both list of dicts (from database/API) and already parsed TemplateButtonsInput objects.
        """
        if v is None:
            return None
        
        # If it's already a TemplateButtonsInput object, return as-is
        if isinstance(v, TemplateButtonsInput):
            return v
        
        # If it's a list of dictionaries (raw JSON from database), parse it
        if isinstance(v, list):
            try:
                return parse_template_buttons(v)
            except Exception as e:
                raise ValueError(f"Invalid buttons data: {e}")
        
        # If it's any other type, it's invalid
        raise ValueError(f"Buttons must be a list of button objects or TemplateButtonsInput, got {type(v)}")
    
    @field_validator('cards', mode='before')
    @classmethod
    def validate_cards(cls, v):
        """
        Convert raw JSON cards data to TemplateCardsInput object.
        Handles both list of dicts (from database/API) and already parsed TemplateCardsInput objects.
        """
        if v is None:
            return None
        
        # If it's already a TemplateCardsInput object, return as-is
        if isinstance(v, TemplateCardsInput):
            return v
        
        # If it's a list of dictionaries (raw JSON from database), parse it
        if isinstance(v, list):
            try:
                return parse_template_cards(v)
            except Exception as e:
                raise ValueError(f"Invalid cards data: {e}")
        
        # If it's any other type, it's invalid
        raise ValueError(f"Cards must be a list of card objects or TemplateCardsInput, got {type(v)}")

    @model_validator(mode='after')
    def validate_cards_buttons_mutual_exclusivity(self):
        """
        Enforce mutual exclusivity between cards and template-level buttons.
        If cards are present, template-level buttons must be None/empty.
        If template-level buttons are present, cards must be None/empty.
        """
        has_cards = self.cards is not None and (
            isinstance(self.cards, TemplateCardsInput) and len(self.cards.cards) > 0
        )
        has_buttons = self.buttons is not None and (
            isinstance(self.buttons, TemplateButtonsInput) and len(self.buttons.buttons) > 0
        )
        
        if has_cards and has_buttons:
            raise ValueError(
                "Templates cannot have both cards and template-level buttons. "
                "When using cards, buttons should be placed inside individual cards only."
            )
        
        return self
    
    

