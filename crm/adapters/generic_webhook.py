"""Generic-webhook connector (#198).

Lets tenants pipe CtwaLead state changes to any HTTPS endpoint with
an HMAC-signed payload. The connector cannot ingest status updates
back (no defined inbound parsing for generic endpoints) — outbound
only.
"""

from __future__ import annotations

import logging
from typing import Optional

from crm.adapters.base import CrmConnector, CrmStatusEvent, register_connector

logger = logging.getLogger(__name__)


@register_connector("generic_webhook")
class GenericWebhookConnector(CrmConnector):
    def push_lead(self, lead, external_event_id: str) -> str:
        # Production:
        #   POST connection.webhook_url
        #   header: X-Jina-External-Event-Id: external_event_id
        #   header: X-Jina-Signature: HMAC-SHA256(body, connection.webhook_secret)
        logger.info(
            "[crm.generic_webhook] STUB push_lead lead=%s event_id=%s url=%s",
            lead.id,
            external_event_id,
            self.connection.webhook_url or "<unset>",
        )
        return f"generic-webhook-{lead.id}-stub"

    def parse_inbound_status(self, payload: dict) -> Optional[CrmStatusEvent]:
        # Outbound-only — generic webhooks can't be parsed back since
        # we don't know the tenant's schema.
        return None

    def verify_inbound_signature(self, request) -> bool:
        return False
