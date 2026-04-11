from typing import Literal, Optional

from pydantic import BaseModel


class BaseTemplateValidator(BaseModel):
    """
    Base Pydantic model for Gupshup template validation.

    Validates the common fields required by all Gupshup template categories
    (MARKETING, UTILITY, AUTHENTICATION) before submission to the
    Gupshup Partner API.
    """

    model_config = {"extra": "ignore"}

    elementName: str
    languageCode: str
    content: Optional[str] = None  # None for CAROUSEL/CATALOG templates
    category: Literal["AUTHENTICATION", "MARKETING", "UTILITY"]
    vertical: str
    example: Optional[str] = None
    exampleHeader: Optional[str] = None
    enableSample: Optional[bool] = True
    allowTemplateCategoryChange: Optional[bool] = False
    message_send_ttl_seconds: Optional[int] = 43200
    # LTO fields (Gupshup uses isLTO, hasExpiration, limitedOfferText)
    isLTO: Optional[bool] = None
    hasExpiration: Optional[bool] = None
    limitedOfferText: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict):
        return cls(**data)
