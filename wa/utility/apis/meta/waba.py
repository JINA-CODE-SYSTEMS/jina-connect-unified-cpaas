from typing import Optional

from wa.utility.apis.meta.base_api import WAAPI


class WABAAPI(WAAPI):
    """
    API client for WhatsApp Business Account (WABA) operations.
    This class provides methods to interact with WABA-specific endpoints,
    extending the base WAAPI class functionality.
    Attributes:
        Inherits all attributes from WAAPI parent class.
    Methods:
        get_waba_details(): Retrieves WhatsApp Business Account details.
    Properties:
        _get_waba_details: Constructs the URL endpoint for fetching WABA information.
    """

    waba_id: Optional[str] = None

    @property
    def _get_waba_details(self):
        return f"{self.BASE_URL}{self.waba_id}?fields=name,status,currency,country,business_verification_status,onboarding_status,marketing_messages_onboarding_status"

    def get_waba_details(self):
        url = self._get_waba_details
        request_data = {
            "method": "GET",
            "url": url,
            "headers": self.headers,
        }
        return self.make_request(request_data)

    # ── App subscription (webhook delivery) ──────────────────────────────
    #
    # Two separate things control whether Meta sends us anything, and only
    # one of them is app-level:
    #
    #   * the callback URL and verify token are configured once per app, in
    #     the App Dashboard — there is no API call for those;
    #   * every WABA must *additionally* be subscribed to that app, or Meta
    #     delivers nothing for it: no messages, no statuses, no template
    #     updates.
    #
    # Only the second is an API operation, and it was missing entirely
    # (#264). Meta derives which app to subscribe from the access token, so
    # no app id is ever sent.

    @property
    def _subscribed_apps_url(self) -> str:
        return f"{self.BASE_URL}{self.waba_id}/subscribed_apps"

    def subscribe_app(self) -> dict:
        """``POST /{waba_id}/subscribed_apps`` — subscribe this token's app.

        Idempotent: Meta returns success for a WABA that is already
        subscribed, so callers need not check first.
        """
        return self.make_request(
            {
                "method": "POST",
                "url": self._subscribed_apps_url,
                "headers": self.headers,
            }
        )

    def get_subscribed_apps(self) -> dict:
        """``GET /{waba_id}/subscribed_apps`` — which apps receive this WABA.

        Each row carries a ``whatsapp_business_api_data`` object with the
        app's ``id``, ``name`` and ``link``.
        """
        return self.make_request(
            {
                "method": "GET",
                "url": self._subscribed_apps_url,
                "headers": self.headers,
            }
        )

    def unsubscribe_app(self) -> dict:
        """``DELETE /{waba_id}/subscribed_apps`` — stop delivery for this WABA."""
        return self.make_request(
            {
                "method": "DELETE",
                "url": self._subscribed_apps_url,
                "headers": self.headers,
            }
        )
