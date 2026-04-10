from enum import Enum
from typing import List, Optional

from pydantic import (BaseModel, Field, HttpUrl, field_validator,
                      model_validator)


class HeaderType(str, Enum):
    """Enum for different header types in template cards"""
    IMAGE = "IMAGE"
    VIDEO = "VIDEO"
    DOCUMENT = "DOCUMENT"


class CardButtonType(str, Enum):
    """Enum for different button types in template cards"""
    URL = "URL"
    QUICK_REPLY = "QUICK_REPLY"
    PHONE_NUMBER = "PHONE_NUMBER"


class TemplateCardButton(BaseModel):
    """
    Template card button class that handles different button types for cards:
    - URL: Website/link buttons with dynamic parameters
    - QUICK_REPLY: Quick reply buttons
    - PHONE_NUMBER: Call buttons
    """
    type: CardButtonType = Field(..., description="Type of button")
    text: str = Field(..., description="Button text displayed to user")
    
    # URL button fields
    url: Optional[str] = Field(None, description="URL for URL type buttons, can contain {{1}}, {{2}} etc for dynamic content")
    buttonValue: Optional[str] = Field(None, description="Base URL value without dynamic parameters")
    suffix: Optional[str] = Field(None, description="Suffix to append to the URL")
    example: Optional[List[str]] = Field(None, description="Example URLs showing how dynamic content would be replaced")
    
    # Phone number button fields  
    phone_number: Optional[str] = Field(None, description="Phone number for PHONE_NUMBER type")
    
    @field_validator('url')
    @classmethod
    def validate_url(cls, v, info):
        if info.data.get('type') == CardButtonType.URL and not v:
            raise ValueError('url is required for URL type buttons')
        return v
    
    @field_validator('phone_number')
    @classmethod
    def validate_phone_number(cls, v, info):
        if info.data.get('type') == CardButtonType.PHONE_NUMBER and not v:
            raise ValueError('phone_number is required for PHONE_NUMBER type buttons')
        return v

    model_config = {
        "populate_by_name": True,
        "json_schema_extra": {
            "examples": [
                {
                    "type": "URL",
                    "text": "Buy now",
                    "url": "https://www.luckyshrub.com/shop?promo={{1}}",
                    "buttonValue": "https://www.luckyshrub.com/shop?promo=",
                    "suffix": "exotic_produce_2023",
                    "example": ["https://www.luckyshrub.com/shop?promo=exotic_produce_2023"]
                },
                {
                    "type": "QUICK_REPLY",
                    "text": "Send more like this"
                },
                {
                    "type": "PHONE_NUMBER", 
                    "text": "Call Us",
                    "phone_number": "+919876543210"
                }
            ]
        }
    }


class TemplateCardInput(BaseModel):
    """
    Template card input class that handles carousel/card templates for WhatsApp:
    - Supports IMAGE, VIDEO, DOCUMENT headers
    - Includes body text with dynamic parameters
    - Contains buttons for user interaction
    """
    headerType: HeaderType = Field(default=HeaderType.IMAGE, description="Type of header media (defaults to IMAGE for carousel cards)")
    mediaUrl: Optional[HttpUrl] = Field(None, description="URL of the media file (for IMAGE/VIDEO/DOCUMENT)")
    mediaId: Optional[str] = Field(None, description="Media ID if uploaded to WhatsApp (alternative to mediaUrl)")
    exampleMedia: Optional[str] = Field(None, description="Example media handle (alternative to mediaUrl/mediaId)")
    
    body: str = Field(..., description="Body text of the card, can contain {{1}}, {{2}} etc for dynamic content")
    sampleText: Optional[str] = Field(None, description="Sample text showing how dynamic content would appear. Auto-derived from body if not provided.")
    
    buttons: List[TemplateCardButton] = Field(..., description="List of buttons for the card (required, max 3)")
    
    @model_validator(mode='after')
    def populate_sample_text(self):
        """Auto-derive sampleText from body when not explicitly provided."""
        import re
        if not self.sampleText and self.body:
            # Strip positional placeholders → generic sample markers
            self.sampleText = re.sub(r'\{\{\d+\}\}', '[sample]', self.body)
        return self
    
    @model_validator(mode='after')
    def validate_media(self):
        # At least one of mediaUrl, mediaId, or exampleMedia should be provided
        if self.headerType in [HeaderType.IMAGE, HeaderType.VIDEO, HeaderType.DOCUMENT]:
            if not self.mediaUrl and not self.mediaId and not self.exampleMedia:
                raise ValueError(f'Either mediaUrl, mediaId, or exampleMedia is required for {self.headerType} header type')
        return self
    
    @field_validator('buttons')
    @classmethod
    def validate_buttons(cls, v):
        if len(v) == 0:
            raise ValueError('At least one button is required')
        if len(v) > 3:  # WhatsApp template limit
            raise ValueError('Maximum 3 buttons allowed per template card')
        return v
    
    @field_validator('body')
    @classmethod
    def validate_body_params(cls, v, info):
        # This will be handled by model validator instead
        return v
    
    @model_validator(mode='after')
    def validate_body_sample_text(self):
        # sampleText is auto-populated by populate_sample_text; this is a safety check
        if '{{' in self.body and not self.sampleText:
            import re
            self.sampleText = re.sub(r'\{\{\d+\}\}', '[sample]', self.body)
        return self

    model_config = {
        "populate_by_name": True,
        "json_schema_extra": {
            "examples": [
                {
                    "headerType": "IMAGE",
                    "mediaUrl": "https://www.buildquickbots.com/whatsapp/media/sample/jpg/sample01.jpg",
                    "mediaId": None,
                    "exampleMedia": None,
                    "body": "New Year is round the corner {{1}}",
                    "sampleText": "New Year is round the corner User",
                    "buttons": [
                        {
                            "type": "URL",
                            "text": "Buy now",
                            "url": "https://www.luckyshrub.com/shop?promo={{1}}",
                            "buttonValue": "https://www.luckyshrub.com/shop?promo=",
                            "suffix": "exotic_produce_2023",
                            "example": ["https://www.luckyshrub.com/shop?promo=exotic_produce_2023"]
                        },
                        {
                            "type": "QUICK_REPLY",
                            "text": "Send more like this"
                        }
                    ]
                },
                {
                    "headerType": "VIDEO",
                    "mediaUrl": None,
                    "mediaId": "6462811350485912",
                    "exampleMedia": None,
                    "body": "Time for shopping yay {{1}} 😀",
                    "sampleText": "Time for shopping yay user 😀",
                    "buttons": [
                        {
                            "type": "URL",
                            "text": "Buy now", 
                            "url": "https://www.luckyshrub.com/shop?promo={{1}}",
                            "buttonValue": "https://www.luckyshrub.com/shop?promo=",
                            "suffix": "exotic_produce_2023",
                            "example": ["https://www.luckyshrub.com/shop?promo=exotic_produce_2023"]
                        },
                        {
                            "type": "QUICK_REPLY",
                            "text": "Send more like this"
                        }
                    ]
                }
            ]
        }
    }


class TemplateCardsInput(BaseModel):
    """Container for multiple template cards (carousel)"""
    cards: List[TemplateCardInput] = Field(..., description="List of template cards")
    
    @field_validator('cards')
    @classmethod
    def validate_cards_count(cls, v):
        if len(v) == 0:
            raise ValueError('At least one card is required')
        if len(v) > 10:  # WhatsApp carousel limit
            raise ValueError('Maximum 10 cards allowed per carousel template')
        return v


# Utility functions for parsing card dictionaries
def parse_template_cards(cards_data: List[dict]) -> TemplateCardsInput:
    """
    Parse a list of card dictionaries into TemplateCardsInput
    
    Args:
        cards_data: List of dictionaries containing card data
        
    Returns:
        TemplateCardsInput: Parsed and validated cards
    
    Example:
        >>> cards = [
        ...     {
        ...         "headerType": "IMAGE",
        ...         "mediaUrl": "https://example.com/image.jpg", 
        ...         "body": "Check this out {{1}}",
        ...         "sampleText": "Check this out User",
        ...         "buttons": [{"type": "QUICK_REPLY", "text": "Yes"}]
        ...     }
        ... ]
        >>> parsed = parse_template_cards(cards)
    """
    return TemplateCardsInput(cards=[TemplateCardInput(**card) for card in cards_data])


def parse_single_template_card(card_data: dict) -> TemplateCardInput:
    """
    Parse a single card dictionary into TemplateCardInput
    
    Args:
        card_data: Dictionary containing card data
        
    Returns:
        TemplateCardInput: Parsed and validated card
        
    Example:
        >>> card = {
        ...     "headerType": "VIDEO",
        ...     "mediaId": "123456", 
        ...     "body": "Watch this {{1}}",
        ...     "sampleText": "Watch this now",
        ...     "buttons": [{"type": "URL", "text": "Learn More", "url": "https://example.com"}]
        ... }
        >>> parsed = parse_single_template_card(card)
    """
    return TemplateCardInput(**card_data)
