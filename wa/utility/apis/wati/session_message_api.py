"""
WATI Session Message API Client

Provides session (open-window) messaging operations via the WATI API.

Session messages can only be sent within a 24-hour window after the customer
last sent a message to the business.

API Endpoints:
    - POST /api/v1/sendSessionMessage/{whatsappNumber}       — Send text message
    - POST /api/v1/sendSessionFile/{whatsappNumber}           — Send file (upload)
    - POST /api/v1/sendSessionFileViaUrl/{whatsappNumber}     — Send file via URL
    - POST /api/v1/sendInteractiveListMessage                 — Send interactive list
    - POST /api/v1/sendInteractiveButtonsMessage              — Send interactive buttons

API Reference: https://docs.wati.io/reference
"""

from typing import Optional

from wa.utility.apis.wati.base_api import WAAPI


class SessionMessageAPI(WAAPI):
    """
    WATI API client for session (open-window) messaging.

    Handles:
    - Sending text messages to open sessions
    - Sending files to open sessions (via upload or URL)
    - Sending interactive list messages
    - Sending interactive button messages
    - Retrieving message history

    Required credentials:
    - api_endpoint: WATI tenant API endpoint
    - token: Bearer token for API authentication
    """

    # =========================================================================
    # URL Properties
    # =========================================================================

    def _send_session_message_url(self, whatsapp_number: str) -> str:
        """URL for sending a text message to an open session."""
        return self.v1_url(f"sendSessionMessage/{whatsapp_number}")

    def _send_session_file_url(self, whatsapp_number: str) -> str:
        """URL for sending a file upload to an open session."""
        return self.v1_url(f"sendSessionFile/{whatsapp_number}")

    def _send_session_file_via_url_url(self, whatsapp_number: str) -> str:
        """URL for sending a file via URL to an open session."""
        return self.v1_url(f"sendSessionFileViaUrl/{whatsapp_number}")

    @property
    def _send_interactive_list_message_url(self) -> str:
        """URL for sending an interactive list message."""
        return self.v1_url("sendInteractiveListMessage")

    @property
    def _send_interactive_buttons_message_url(self) -> str:
        """URL for sending an interactive buttons message."""
        return self.v1_url("sendInteractiveButtonsMessage")

    def _get_messages_url(self, whatsapp_number: str) -> str:
        """URL for retrieving messages by WhatsApp number."""
        return self.v1_url(f"getMessages/{whatsapp_number}")

    def _get_message_url(self, phone_number: str, local_message_id: str) -> str:
        """URL for retrieving a specific message by phone number and local message ID."""
        return self.v1_url(f"whatsApp/messages/{phone_number}/{local_message_id}")

    # =========================================================================
    # Session Message Operations
    # =========================================================================

    def send_session_message(
        self,
        whatsapp_number: str,
        message_text: str,
        reply_context_id: Optional[str] = None,
        channel_phone_number: Optional[str] = None,
        local_message_id: Optional[str] = None,
    ) -> dict:
        """
        Send a text message to an open session.

        Args:
            whatsapp_number: Recipient WhatsApp number with country code.
            message_text: Message text to send (max 4096 chars).
            reply_context_id: WhatsApp message ID (wamid) to reply to.
            channel_phone_number: Channel phone number with country code.
            local_message_id: Unique message identifier for tracking.

        Returns:
            dict: Response from WATI API.
        """
        url = self._send_session_message_url(whatsapp_number)
        params = {"messageText": message_text}
        if reply_context_id:
            params["replyContextId"] = reply_context_id
        if channel_phone_number:
            params["channelPhoneNumber"] = channel_phone_number
        if local_message_id:
            params["localMessageId"] = local_message_id

        request_data = {
            "method": "POST",
            "url": url,
            "headers": self.json_headers,
            "params": params,
        }
        return self.make_json_request(request_data)

    def send_session_file(
        self,
        whatsapp_number: str,
        file_path: str,
        file_type: Optional[str] = None,
    ) -> dict:
        """
        Send a file to an open session via file upload.

        Args:
            whatsapp_number: Recipient WhatsApp number with country code.
            file_path: Path to the file to upload.
            file_type: MIME type of the file (auto-detected if not provided).

        Returns:
            dict: Response from WATI API.
        """
        import mimetypes
        import os

        import requests

        url = self._send_session_file_url(whatsapp_number)

        # Auto-detect file type if not provided
        if not file_type:
            file_type, _ = mimetypes.guess_type(file_path)
            if not file_type:
                ext = os.path.splitext(file_path)[1].lower()
                mime_map = {
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".png": "image/png",
                    ".mp4": "video/mp4",
                    ".pdf": "application/pdf",
                    ".ogg": "audio/ogg",
                    ".mp3": "audio/mpeg",
                }
                file_type = mime_map.get(ext, "application/octet-stream")

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }

        with open(file_path, "rb") as f:
            files = {"file": f}

            # Print curl equivalent for debugging
            print("=" * 80)
            print("EQUIVALENT CURL COMMAND FOR WATI SESSION FILE UPLOAD:")
            print("=" * 80)
            print(f"curl --location --request POST '{url}' \\")
            print(f"  --header 'Authorization: Bearer {self.token}' \\")
            print(f"  --form 'file=@\"{file_path}\"'")
            print("=" * 80)

            response = requests.post(url, headers=headers, files=files)

        if response.status_code not in [200, 201]:
            error_msg = f"Session file upload failed with status code {response.status_code}"
            try:
                error_details = response.json()
                error_msg += f"\nResponse: {error_details}"
            except Exception:
                error_msg += f"\nResponse text: {response.text}"
            raise Exception(error_msg)

        return response.json()

    def send_session_file_via_url(
        self,
        whatsapp_number: str,
        file_url: str,
        file_name: Optional[str] = None,
    ) -> dict:
        """
        Send a file to an open session via URL.

        Args:
            whatsapp_number: Recipient WhatsApp number with country code.
            file_url: Public URL of the file to send.
            file_name: Optional filename override.

        Returns:
            dict: Response from WATI API.
        """
        url = self._send_session_file_via_url_url(whatsapp_number)
        data = {"url": file_url}
        if file_name:
            data["fileName"] = file_name

        request_data = {
            "method": "POST",
            "url": url,
            "headers": self.json_headers,
            "data": data,
        }
        return self.make_json_request(request_data)

    def send_interactive_list_message(self, data: dict) -> dict:
        """
        Send an interactive list message.

        Args:
            data: Interactive list message payload.

        Returns:
            dict: Response from WATI API.
        """
        request_data = {
            "method": "POST",
            "url": self._send_interactive_list_message_url,
            "headers": self.json_headers,
            "data": data,
        }
        return self.make_json_request(request_data)

    def send_interactive_buttons_message(self, data: dict) -> dict:
        """
        Send an interactive buttons message.

        Args:
            data: Interactive buttons message payload.

        Returns:
            dict: Response from WATI API.
        """
        request_data = {
            "method": "POST",
            "url": self._send_interactive_buttons_message_url,
            "headers": self.json_headers,
            "data": data,
        }
        return self.make_json_request(request_data)

    # =========================================================================
    # Message Retrieval
    # =========================================================================

    def get_messages(self, whatsapp_number: str) -> dict:
        """
        Get messages by WhatsApp number.

        Args:
            whatsapp_number: WhatsApp number with country code.

        Returns:
            dict: Messages from WATI API.
        """
        url = self._get_messages_url(whatsapp_number)
        request_data = {
            "method": "GET",
            "url": url,
            "headers": self.json_headers,
        }
        return self.make_json_request(request_data)

    def get_message(self, phone_number: str, local_message_id: str) -> dict:
        """
        Get a specific message by phone number and local message ID.

        Args:
            phone_number: Phone number with country code.
            local_message_id: Local message identifier.

        Returns:
            dict: Message details from WATI API.
        """
        url = self._get_message_url(phone_number, local_message_id)
        request_data = {
            "method": "GET",
            "url": url,
            "headers": self.json_headers,
        }
        return self.make_json_request(request_data)
