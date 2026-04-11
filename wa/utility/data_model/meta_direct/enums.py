"""
Enums for META Direct API Template Validation
"""

from enum import Enum


class ParameterFormat(str, Enum):
    """Parameter format types supported by META"""

    NAMED = "NAMED"
    POSITIONAL = "POSITIONAL"


class HeaderFormat(str, Enum):
    """Header format types"""

    TEXT = "TEXT"
    IMAGE = "IMAGE"
    VIDEO = "VIDEO"
    DOCUMENT = "DOCUMENT"
    LOCATION = "LOCATION"


class ButtonType(str, Enum):
    """Button types supported in utility templates"""

    URL = "url"
    PHONE_NUMBER = "phone_number"
    QUICK_REPLY = "quick_reply"
    COPY_CODE = "copy_code"
    FLOW = "flow"
    CATALOG = "CATALOG"


class ComponentType(str, Enum):
    """Component types in a template"""

    HEADER = "HEADER"
    BODY = "BODY"
    FOOTER = "FOOTER"
    BUTTONS = "BUTTONS"
    CALL_PERMISSION_REQUEST = "call_permission_request"


class TemplateCategory(str, Enum):
    """Template category types"""

    AUTHENTICATION = "AUTHENTICATION"
    MARKETING = "MARKETING"
    UTILITY = "UTILITY"


class TemplateType(str, Enum):
    """
    Internal template type for UI/storage purposes.

    This is NOT sent to META API - it's for internal tracking of
    what kind of media/content the template uses.

    Maps to legacy TemplateTypeChoices in wa/models.py
    """

    TEXT = "TEXT"
    IMAGE = "IMAGE"
    VIDEO = "VIDEO"
    DOCUMENT = "DOCUMENT"
    LOCATION = "LOCATION"
    PRODUCT = "PRODUCT"
    CATALOG = "CATALOG"
    AUDIO = "AUDIO"
    CAROUSEL = "CAROUSEL"
