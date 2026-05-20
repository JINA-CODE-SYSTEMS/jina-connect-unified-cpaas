"""HubSpot connector (#198). API calls stubbed pending HubSpot app creation."""

from __future__ import annotations

import logging
from typing import Optional

from crm.adapters.base import CrmConnector, CrmStatusEvent, register_connector

logger = logging.getLogger(__name__)


@register_connector("hubspot")
class HubSpotConnector(CrmConnector):
    """HubSpot adapter.

    Production fills in:
      * OAuth refresh on ``access_token`` expiry
      * ``POST /crm/v3/objects/contacts`` for push_lead
      * Webhook signature: ``X-HubSpot-Signature-V3`` HMAC-SHA256 of
        ``utf8(timestamp + method + uri + body)``.

    Stubbed here so the module imports cleanly and tests pass.
    """

    def push_lead(self, lead, external_event_id: str) -> str:
        # Production: POST to HubSpot with the lead payload, include
        # ``properties.jina_external_event_id = external_event_id`` so
        # the inbound webhook handler can dedupe echoes.
        logger.info("[crm.hubspot] STUB push_lead lead=%s event_id=%s", lead.id, external_event_id)
        return f"hubspot-contact-{lead.id}-stub"

    def parse_inbound_status(self, payload: dict) -> Optional[CrmStatusEvent]:
        # Connector contract: this method MUST NOT raise. Wrap the
        # actual parsing so a malformed payload yields ``None`` rather
        # than crashing the inbound view. (#201 second review High #2)
        try:
            return self._parse_inbound_status_inner(payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[crm.hubspot] parse_inbound_status failed: %s", exc)
            return None

    @staticmethod
    def _extract_prop(props: dict, name: str) -> str:
        """Read a HubSpot property tolerant of both wire shapes:

          * Subscription webhook: ``{"propname": {"value": "..."}}``
          * Workflow / flat delivery: ``{"propname": "..."}``

        Returns ``""`` on missing key or unexpected value type. (#201
        second review High #2)
        """
        raw = props.get(name)
        if isinstance(raw, dict):
            return str(raw.get("value", "") or "")
        if isinstance(raw, (str, int, float)):
            return str(raw)
        return ""

    def _parse_inbound_status_inner(self, payload: dict) -> Optional[CrmStatusEvent]:
        if not isinstance(payload, dict):
            return None
        # HubSpot webhooks arrive in two shapes:
        #   1. Subscription webhooks: {"events": [...]} with multiple
        #      events per delivery — we walk the list.
        #   2. Per-event delivery (e.g. via Workflow): payload IS the
        #      event itself — wrap it as a one-element list so the
        #      iteration below handles both.
        events = payload.get("events") or [payload]
        for event in events:
            if not isinstance(event, dict):
                continue
            if event.get("subscriptionType") not in ("contact.propertyChange", None):
                continue
            props = event.get("properties") or {}
            if not isinstance(props, dict):
                continue
            crm_external_id = str(event.get("objectId") or "")
            external_event_id = self._extract_prop(props, "jina_external_event_id")
            new_status = self._extract_prop(props, "lifecyclestage")
            if not (crm_external_id and new_status):
                continue
            return CrmStatusEvent(
                external_event_id=external_event_id,
                crm_external_id=crm_external_id,
                new_status=new_status,
                raw_payload=event,
            )
        return None

    def verify_inbound_signature(self, request) -> bool:
        """HubSpot V3 signature verification.

        v1 of this method implemented a simpler ``HMAC-SHA256(body)``
        scheme which doesn't match HubSpot's published V3 spec —
        legitimate webhooks would have been rejected.

        Rather than ship a half-implementation that silently looks
        correct in a future review, fail closed via
        ``NotImplementedError`` so production *cannot* turn this
        connector on until the full V3 algorithm
        (``HMAC-SHA256(utf8(method + uri + body + timestamp))``) lands
        — including the ``X-HubSpot-Request-Timestamp`` replay window
        check. (#201 second review Medium #6)
        """
        raise NotImplementedError(
            "HubSpot V3 signature verification is not yet implemented. "
            "Do not enable this connector in production until "
            "verify_inbound_signature does the full V3 HMAC + replay "
            "window check. See HubSpot docs: "
            "https://developers.hubspot.com/docs/api/webhooks/validating-requests"
        )
