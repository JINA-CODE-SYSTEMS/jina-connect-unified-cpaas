"""
META Direct (Cloud API) Session Message API.

Sends free-form (session) messages via META's Cloud API.

Endpoint: POST https://graph.facebook.com/{version}/{phone_number_id}/messages

Session messages can only be sent within the 24-hour customer-care window
(i.e. after the contact has last messaged you).

The payload format is identical to Gupshup's Cloud API proxy — both accept
the standard WhatsApp Cloud API JSON schema (messaging_product, to, type, …).
"""

from typing import Any, Optional, Union

from wa.utility.apis.meta.base_api import WAAPI


class SessionMessageAPI(WAAPI):
    """
    Send free-form (non-template) messages via META Cloud API.

    Required fields:
        token:            META access token (Bearer)
        phone_number_id:  The sender phone-number ID on META

    Usage::

        api = SessionMessageAPI(
            token="EAAx…",
            phone_number_id="YOUR_PHONE_NUMBER_ID",
        )
        result = api.send_message({
            "messaging_product": "whatsapp",
            "to": "919876543210",
            "type": "text",
            "text": {"body": "Hello from META!"}
        })
    """

    phone_number_id: str

    # ── URL helpers ───────────────────────────────────────────────────────

    @property
    def _send_message_url(self) -> str:
        """POST /{phone_number_id}/messages"""
        return f"{self.BASE_URL}{self.phone_number_id}/messages"

    # ── Public API ────────────────────────────────────────────────────────

    def send_message(self, data: Any) -> dict:
        """
        Send a session (free-form) message.

        Args:
            data: Cloud API message payload (dict or Pydantic model with
                  ``.model_dump(by_alias=True, exclude_none=True)``).

        Returns:
            dict – META response, typically::

                {"messaging_product": "whatsapp",
                 "contacts": [{"wa_id": "919…"}],
                 "messages": [{"id": "wamid.HBg…"}]}

        Raises:
            Exception: on non-200/201 response (raised by ``make_json_request``).
        """
        from wa.utility.data_model.gupshup.session_message_base import \
            SessionMessageBase

        if isinstance(data, SessionMessageBase):
            data = data.model_dump(by_alias=True, exclude_none=True)

        url = self._send_message_url
        request_data = {
            "method": "POST",
            "url": url,
            "headers": self.json_headers,
            "data": data,
        }
        return self.make_json_request(request_data)
