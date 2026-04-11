"""
WATI Template API Client

Provides template CRUD operations and template message sending via the WATI API.

API Endpoints:
    - GET    /api/v1/getMessageTemplates          — List all templates
    - GET    /api/v2/getMessageTemplates           — List templates (v2, paginated)
    - POST   /api/v1/whatsApp/templates            — Create a new template
    - DELETE /api/v1/whatsApp/templates/{wabaId}/{name}            — Delete templates by name
    - DELETE /api/v1/whatsApp/templates/{wabaId}/{name}/{language} — Delete specific template
    - POST   /api/v1/sendTemplateMessage            — Send single template message
    - POST   /api/v1/sendTemplateMessages           — Send bulk template messages
    - POST   /api/v2/sendTemplateMessage            — Send template message (v2 Beta)
    - POST   /api/v2/sendTemplateMessages           — Send bulk template messages (v2 Beta)

API Reference: https://docs.wati.io/reference
"""

from typing import Optional

from wa.utility.apis.wati.base_api import WAAPI


class TemplateAPI(WAAPI):
    """
    WATI API client for WhatsApp template operations.

    Handles:
    - Template listing and retrieval
    - Template creation and deletion
    - Template message sending (single and bulk)

    Required credentials:
    - api_endpoint: WATI tenant API endpoint (e.g., "your-tenant.wati.io")
    - token: Bearer token for API authentication
    """

    # =========================================================================
    # URL Properties
    # =========================================================================

    @property
    def _get_templates_v1(self) -> str:
        """URL for listing templates (v1)."""
        return self.v1_url("getMessageTemplates")

    @property
    def _get_templates_v2(self) -> str:
        """URL for listing templates (v2 with pagination)."""
        return self.v2_url("getMessageTemplates")

    @property
    def _create_template(self) -> str:
        """URL for creating a new template."""
        return self.v1_url("whatsApp/templates")

    def _delete_template_by_name_url(self, waba_id: str, name: str) -> str:
        """URL for deleting all templates with a given name."""
        return self.v1_url(f"whatsApp/templates/{waba_id}/{name}")

    def _delete_template_url(self, waba_id: str, name: str, language: str) -> str:
        """URL for deleting a specific template by name and language."""
        return self.v1_url(f"whatsApp/templates/{waba_id}/{name}/{language}")

    @property
    def _send_template_message_v1(self) -> str:
        """URL for sending a single template message (v1)."""
        return self.v1_url("sendTemplateMessage")

    @property
    def _send_template_message_v2(self) -> str:
        """URL for sending a single template message (v2 Beta)."""
        return self.v2_url("sendTemplateMessage")

    @property
    def _send_template_messages_v1(self) -> str:
        """URL for sending bulk template messages (v1)."""
        return self.v1_url("sendTemplateMessages")

    @property
    def _send_template_messages_v2(self) -> str:
        """URL for sending bulk template messages (v2 Beta)."""
        return self.v2_url("sendTemplateMessages")

    # =========================================================================
    # Template CRUD Operations
    # =========================================================================

    def get_templates(
        self,
        page_size: Optional[int] = None,
        page_number: Optional[int] = None,
        channel_phone_number: Optional[str] = None,
    ) -> dict:
        """
        Get all message templates (v1).

        Args:
            page_size: Number of templates per page.
            page_number: Page number to retrieve.
            channel_phone_number: Filter by channel phone number.

        Returns:
            dict: List of templates from WATI.
        """
        params = {}
        if page_size is not None:
            params["pageSize"] = page_size
        if page_number is not None:
            params["pageNumber"] = page_number
        if channel_phone_number:
            params["channelPhoneNumber"] = channel_phone_number

        request_data = {
            "method": "GET",
            "url": self._get_templates_v1,
            "headers": self.json_headers,
            "params": params,
        }
        return self.make_json_request(request_data)

    def get_templates_v2(
        self,
        page_size: Optional[int] = None,
        page_number: Optional[int] = None,
        channel_phone_number: Optional[str] = None,
    ) -> dict:
        """
        Get all message templates (v2 with pagination).

        Args:
            page_size: Number of templates per page.
            page_number: Page number to retrieve.
            channel_phone_number: Filter by channel phone number.

        Returns:
            dict: Paginated list of templates from WATI.
        """
        params = {}
        if page_size is not None:
            params["pageSize"] = page_size
        if page_number is not None:
            params["pageNumber"] = page_number
        if channel_phone_number:
            params["channelPhoneNumber"] = channel_phone_number

        request_data = {
            "method": "GET",
            "url": self._get_templates_v2,
            "headers": self.json_headers,
            "params": params,
        }
        return self.make_json_request(request_data)

    def create_template(self, data: dict) -> dict:
        """
        Create a new WhatsApp template via WATI.

        Uses JSON body as required by WATI's template creation API.

        Args:
            data: Template payload with fields like:
                - type (str): Template type (e.g., "template")
                - category (str): MARKETING, UTILITY, AUTHENTICATION
                - subCategory (str): STANDARD, CAROUSEL, CATALOG, etc.
                - buttonsType (str): NONE, quick_reply, call_to_action, etc.
                - buttons (list): List of button objects
                - footer (str): Footer text
                - elementName (str): Internal template name
                - language (str): Language code (e.g., "en")
                - header (dict): Header configuration
                - body (str): Body text with variables like {{name}}
                - customParams (list): Custom parameter definitions
                - creationMethod (int): 0=HUMAN, 1=AI, 2=HUMAN_AND_AI

        Returns:
            dict: Response from WATI API with template details on success.
        """
        request_data = {
            "method": "POST",
            "url": self._create_template,
            "headers": self.json_headers,
            "data": data,
        }
        return self.make_json_request(request_data)

    def delete_templates_by_name(self, waba_id: str, name: str) -> dict:
        """
        Delete all templates with a given name.

        Args:
            waba_id: WhatsApp Business Account ID.
            name: Template name to delete.

        Returns:
            dict: Response from WATI API.
        """
        url = self._delete_template_by_name_url(waba_id, name)
        request_data = {
            "method": "DELETE",
            "url": url,
            "headers": self.json_headers,
        }
        return self.make_json_request(request_data)

    def delete_template(self, waba_id: str, name: str, language: str) -> dict:
        """
        Delete a specific template by name and language.

        Args:
            waba_id: WhatsApp Business Account ID.
            name: Template name.
            language: Language code of the template.

        Returns:
            dict: Response from WATI API.
        """
        url = self._delete_template_url(waba_id, name, language)
        request_data = {
            "method": "DELETE",
            "url": url,
            "headers": self.json_headers,
        }
        return self.make_json_request(request_data)

    # =========================================================================
    # Template Message Sending
    # =========================================================================

    def send_template_message(
        self,
        whatsapp_number: str,
        data: dict,
        use_v2: bool = False,
    ) -> dict:
        """
        Send a single template message.

        Args:
            whatsapp_number: Recipient WhatsApp number with country code (e.g., "85264318721").
            data: Template message payload with:
                - template_name (str): Name of the approved template.
                - broadcast_name (str): Name for broadcast tracking.
                - channel_number (str): Channel phone number.
                - parameters (list[dict]): Parameter values for template variables.
            use_v2: If True, use v2 Beta endpoint.

        Returns:
            dict: Response from WATI API.
        """
        url = self._send_template_message_v2 if use_v2 else self._send_template_message_v1
        request_data = {
            "method": "POST",
            "url": url,
            "headers": self.json_headers,
            "data": data,
            "params": {"whatsappNumber": whatsapp_number},
        }
        return self.make_json_request(request_data)

    def send_template_messages(
        self,
        data: dict,
        use_v2: bool = False,
    ) -> dict:
        """
        Send bulk template messages.

        Args:
            data: Bulk template message payload.
            use_v2: If True, use v2 Beta endpoint.

        Returns:
            dict: Response from WATI API.
        """
        url = self._send_template_messages_v2 if use_v2 else self._send_template_messages_v1
        request_data = {
            "method": "POST",
            "url": url,
            "headers": self.json_headers,
            "data": data,
        }
        return self.make_json_request(request_data)
