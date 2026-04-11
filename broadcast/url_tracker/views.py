"""
Public redirect view for tracked URLs.

Endpoint: GET /r/<code>/

This is a public, unauthenticated endpoint. When a WhatsApp user taps a URL
button, their browser opens this URL. We:
1. Look up the TrackedURL by short code
2. Record the click (IP, user-agent, timestamp)
3. 302-redirect to the original destination URL

If the code is invalid, we return a simple 404.
"""

import logging

from django.http import Http404, HttpResponseRedirect
from django.views import View

from broadcast.url_tracker.models import TrackedURL

logger = logging.getLogger("broadcast.url_tracker")


class TrackedURLRedirectView(View):
    """
    Public redirect endpoint.
    GET /r/<code>/ → 302 → original URL
    """

    def get(self, request, code: str):
        try:
            tracked_url = TrackedURL.objects.get(code=code)
        except TrackedURL.DoesNotExist:
            logger.warning(f"Tracked URL not found: code={code}")
            raise Http404("Link not found or has expired.")

        # Extract click metadata
        ip_address = self._get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")
        referer = request.META.get("HTTP_REFERER", "")

        # Record the click (async-safe: uses atomic F() update)
        try:
            tracked_url.record_click(
                ip_address=ip_address,
                user_agent=user_agent,
                referer=referer,
            )
            logger.info(
                f"Click recorded: code={code}, ip={ip_address}, "
                f"contact={tracked_url.contact_id}, "
                f"broadcast={tracked_url.broadcast_id}"
            )
        except Exception as e:
            # Never block the redirect — log and continue
            logger.exception(f"Error recording click for code={code}: {e}")

        return HttpResponseRedirect(tracked_url.original_url)

    @staticmethod
    def _get_client_ip(request) -> str:
        """Extract client IP, respecting X-Forwarded-For from reverse proxies."""
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            return x_forwarded_for.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR", "")
