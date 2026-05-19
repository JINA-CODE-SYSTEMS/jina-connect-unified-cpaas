"""Salesforce connector (#198). API calls stubbed pending SF app creation.

Salesforce webhooks are notoriously unreliable — Outbound Messages and
Platform Events both retry on any 5xx without idempotency keys of
their own, so the framework-level ``external_event_id`` dedup is the
only thing standing between us and infinite-loop pushes. Don't remove.
"""

from __future__ import annotations

import logging
from typing import Optional

from crm.adapters.base import CrmConnector, CrmStatusEvent, register_connector

logger = logging.getLogger(__name__)


@register_connector("salesforce")
class SalesforceConnector(CrmConnector):
    def push_lead(self, lead, external_event_id: str) -> str:
        # Production:
        #   POST /services/data/vXX.X/sobjects/Lead/
        #   body: {..., Jina_External_Event_Id__c: external_event_id}
        # Salesforce returns the new SObject id which we store as
        # CtwaLead.crm_external_id.
        logger.info("[crm.salesforce] STUB push_lead lead=%s event_id=%s", lead.id, external_event_id)
        return f"sf-lead-{lead.id}-stub"

    def parse_inbound_status(self, payload: dict) -> Optional[CrmStatusEvent]:
        # Salesforce Outbound Message wraps the SObject in a SOAP-ish
        # envelope; in practice clients flatten it before forwarding to
        # our HTTPS endpoint. We expect a flat payload here. Production
        # handles both shapes.
        if not isinstance(payload, dict):
            return None
        sobj = payload.get("sobject") or payload
        if not isinstance(sobj, dict):
            return None
        crm_external_id = str(sobj.get("Id") or "")
        external_event_id = str(sobj.get("Jina_External_Event_Id__c") or "")
        new_status = str(sobj.get("Status") or sobj.get("LeadStatus") or "")
        if not (crm_external_id and new_status):
            return None
        return CrmStatusEvent(
            external_event_id=external_event_id,
            crm_external_id=crm_external_id,
            new_status=new_status,
            raw_payload=sobj,
        )

    def verify_inbound_signature(self, request) -> bool:
        # Salesforce Outbound Messages don't sign — clients commonly
        # gate via IP allowlist + mutual TLS. For Platform Events
        # forwarded via Webhook Relay etc., the relay signs. Accept
        # any request when ``webhook_secret`` is empty (trust IP),
        # require HMAC match when it's set.
        secret = (self.connection.webhook_secret or "").encode("utf-8")
        if not secret:
            return True
        import hashlib
        import hmac

        signature = request.META.get("HTTP_X_SF_SIGNATURE", "")
        if not signature:
            return False
        body = request.body or b""
        computed = hmac.new(secret, body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(computed, signature)
