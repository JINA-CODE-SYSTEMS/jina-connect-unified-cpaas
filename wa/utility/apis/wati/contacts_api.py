"""
WATI Contacts API Client

Provides contact management operations via the WATI API.

API Endpoints:
    - GET    /api/v1/getContacts                                 — List contacts
    - POST   /api/v1/addContact/{whatsappNumber}                 — Add a contact
    - POST   /api/v1/updateContactAttributes/{whatsappNumber}    — Update contact attributes

API Reference: https://docs.wati.io/reference
"""

from typing import Optional

from wa.utility.apis.wati.base_api import WAAPI


class ContactsAPI(WAAPI):
    """
    WATI API client for contact management operations.

    Handles:
    - Listing contacts
    - Adding new contacts
    - Updating contact attributes

    Required credentials:
    - api_endpoint: WATI tenant API endpoint
    - token: Bearer token for API authentication
    """

    # =========================================================================
    # URL Properties
    # =========================================================================

    @property
    def _get_contacts_url(self) -> str:
        """URL for listing contacts."""
        return self.v1_url("getContacts")

    def _add_contact_url(self, whatsapp_number: str) -> str:
        """URL for adding a contact."""
        return self.v1_url(f"addContact/{whatsapp_number}")

    def _update_contact_attributes_url(self, whatsapp_number: str) -> str:
        """URL for updating contact attributes."""
        return self.v1_url(f"updateContactAttributes/{whatsapp_number}")

    # =========================================================================
    # Contact Operations
    # =========================================================================

    def get_contacts(
        self,
        page_size: Optional[int] = None,
        page_number: Optional[int] = None,
        name: Optional[str] = None,
    ) -> dict:
        """
        List all contacts.

        Args:
            page_size: Number of contacts per page.
            page_number: Page number to retrieve.
            name: Filter contacts by name.

        Returns:
            dict: Contacts list from WATI API.
        """
        params = {}
        if page_size is not None:
            params["pageSize"] = page_size
        if page_number is not None:
            params["pageNumber"] = page_number
        if name:
            params["name"] = name

        request_data = {
            "method": "GET",
            "url": self._get_contacts_url,
            "headers": self.json_headers,
            "params": params,
        }
        return self.make_json_request(request_data)

    def add_contact(
        self,
        whatsapp_number: str,
        data: Optional[dict] = None,
    ) -> dict:
        """
        Add a new contact.

        Args:
            whatsapp_number: WhatsApp number with country code.
            data: Optional contact data (name, custom parameters, etc.).

        Returns:
            dict: Response from WATI API.
        """
        url = self._add_contact_url(whatsapp_number)
        request_data = {
            "method": "POST",
            "url": url,
            "headers": self.json_headers,
            "data": data or {},
        }
        return self.make_json_request(request_data)

    def update_contact_attributes(
        self,
        whatsapp_number: str,
        data: dict,
    ) -> dict:
        """
        Update contact attributes.

        Args:
            whatsapp_number: WhatsApp number with country code.
            data: Contact attributes to update.

        Returns:
            dict: Response from WATI API.
        """
        url = self._update_contact_attributes_url(whatsapp_number)
        request_data = {
            "method": "POST",
            "url": url,
            "headers": self.json_headers,
            "data": data,
        }
        return self.make_json_request(request_data)
