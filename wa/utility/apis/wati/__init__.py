"""
WATI API Clients

This package contains API client classes for interacting with the WATI
WhatsApp Business Solution Provider (BSP).

WATI API Reference: https://docs.wati.io/reference

Modules:
- base_api: Base WATI API client with authentication and HTTP methods
- template_api: Template CRUD and sending operations
- session_message_api: Session (open-window) messaging
- media_api: Media upload/download operations
- contacts_api: Contact management operations
"""

from wa.utility.apis.wati.base_api import WAAPI
from wa.utility.apis.wati.contacts_api import ContactsAPI
from wa.utility.apis.wati.media_api import MediaAPI
from wa.utility.apis.wati.session_message_api import SessionMessageAPI
from wa.utility.apis.wati.template_api import TemplateAPI

__all__ = [
    "WAAPI",
    "TemplateAPI",
    "SessionMessageAPI",
    "MediaAPI",
    "ContactsAPI",
]
