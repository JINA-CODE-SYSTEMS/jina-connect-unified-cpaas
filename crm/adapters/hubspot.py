"""HubSpot connector (#198). API calls stubbed pending HubSpot app creation."""

from __future__ import annotations

import hashlib
import hmac
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
        timestamp + method + uri + body.

    Stubbed here so the module imports cleanly and tests pass.
    """

    def push_lead(self, lead, external_event_id: str) -> str:
        # Production: POST to HubSpot with the lead payload, include
        # ``properties.jina_external_event_id = external_event_id`` so
        # the inbound webhook handler can dedupe echoes.
        logger.info("[crm.hubspot] STUB push_lead lead=%s event_id=%s", lead.id, external_event_id)
        return f"hubspot-contact-{lead.id}-stub"

    def parse_inbound_status(self, payload: dict) -> Optional[CrmStatusEvent]:
        if not isinstance(payload, dict):
            return None
        # HubSpot webhooks arrive as arrays of events; take the first
        # status-change event we recognise. Production handles the full
        # array + sub-types.
        events = payload.get("events") or [payload]
        for event in events:
            if not isinstance(event, dict):
                continue
            if event.get("subscriptionType") not in ("contact.propertyChange", None):
                continue
            props = event.get("properties") or {}
            crm_external_id = str(event.get("objectId") or "")
            external_event_id = str(props.get("jina_external_event_id", {}).get("value", ""))
            new_status = str(props.get("lifecyclestage", {}).get("value", ""))
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
        # HubSpot V3 signature spec — full impl in prod.
        secret = (self.connection.webhook_secret or "").encode("utf-8")
        signature = request.META.get("HTTP_X_HUBSPOT_SIGNATURE_V3", "")
        if not secret or not signature:
            return False
        body = request.body or b""
        computed = hmac.new(secret, body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(computed, signature)
