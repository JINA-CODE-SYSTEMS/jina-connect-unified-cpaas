"""CRM connector ABC + registry (#198).

Mirrors the existing ``voice.adapters.registry`` pattern. Each provider
adapter decorates its class with ``@register_connector("provider_name")``
and the connector facade resolves the right one at runtime.

Loop-hazard protection: every outbound push generates a UUID
``external_event_id`` that's included in the CRM-side payload. Inbound
webhooks carrying the same id are dropped — they're our own push
echoing back. See ``crm.adapters.base.push_lead_idempotent`` for the
canonical entry point.
"""

from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from crm.models import CrmConnection
    from ctwa.models import CtwaLead


@dataclass(frozen=True)
class CrmStatusEvent:
    """Parsed status change from an inbound CRM webhook."""

    external_event_id: str
    crm_external_id: str
    new_status: str  # CRM-side status; mapped to Jina-side via CrmEntityMapping
    raw_payload: dict


_REGISTRY: dict[str, type["CrmConnector"]] = {}


def register_connector(provider: str):
    def _wrap(cls: type["CrmConnector"]) -> type["CrmConnector"]:
        existing = _REGISTRY.get(provider)
        if existing is not None and existing is not cls:
            raise RuntimeError(
                f"CRM connector {provider!r} already registered to "
                f"{existing.__qualname__}; refusing to replace with {cls.__qualname__}"
            )
        cls.provider = provider
        _REGISTRY[provider] = cls
        return cls

    return _wrap


def get_connector(connection: "CrmConnection") -> "CrmConnector":
    """Resolve the connector class for *connection*'s provider and
    instantiate it with the connection."""
    cls = _REGISTRY.get(connection.provider)
    if cls is None:
        raise LookupError(f"No CRM connector registered for {connection.provider!r}")
    return cls(connection)


class CrmConnector(ABC):
    """ABC every CRM provider adapter subclasses."""

    provider: str = ""

    def __init__(self, connection: "CrmConnection") -> None:
        self.connection = connection

    @abstractmethod
    def push_lead(self, lead: "CtwaLead", external_event_id: str) -> str:
        """Send a CtwaLead to the CRM. Returns the CRM's internal ID
        for the created/updated contact-or-lead.

        Must include ``external_event_id`` in the CRM-side payload so
        the inbound webhook handler can dedupe our own echoes (see
        ``parse_inbound_status``).
        """

    @abstractmethod
    def parse_inbound_status(self, payload: dict) -> Optional[CrmStatusEvent]:
        """Extract a status change from a CRM webhook payload. Return
        ``None`` if the webhook isn't a status change we care about."""

    @abstractmethod
    def verify_inbound_signature(self, request) -> bool:
        """Provider-specific HMAC verification."""


def push_lead_idempotent(*, connection: "CrmConnection", lead: "CtwaLead") -> str:
    """High-level entry point used by ``ctwa/`` signals.

    Generates a fresh ``external_event_id``, **persists it on the lead
    BEFORE the HTTP push** so an inbound webhook echo that arrives
    while the push is in flight (HubSpot's create-confirmation webhook
    often does this within milliseconds) is correctly deduped even if
    our worker dies between push and post-push save. (#201 review)

    The order of operations:

      1. UUID generated, stamped on ``CtwaLead.last_crm_external_event_id``,
         committed in its own transaction.
      2. HTTP push to the CRM (long-running, fail-prone).
      3. On success, record CRM-side id; on failure, the dedup stamp
         persists so any echo still gets dropped.
    """
    from crm.models import CrmSyncEvent

    event_id = uuid.uuid4().hex

    # ── (1) Stamp the dedup key in its own commit ────────────────────
    # If we crash before reaching the push, the stamp is harmlessly
    # orphaned — there's no echo to dedupe. If we crash *after* push
    # but before the post-push save, the stamp still covers any echo.
    lead.last_crm_external_event_id = event_id
    lead.save(update_fields=["last_crm_external_event_id", "updated_at"])

    # ── (2) HTTP push ────────────────────────────────────────────────
    connector = get_connector(connection)
    try:
        crm_id = connector.push_lead(lead, external_event_id=event_id)
    except Exception as exc:  # noqa: BLE001 — push errors are recoverable
        logger.exception("[crm.push_lead] failed for lead=%s: %s", lead.id, exc)
        CrmSyncEvent.objects.create(
            connection=connection,
            direction="outbound",
            external_event_id=event_id,
            payload={"error": str(exc)[:500]},
            processed=False,
            lead=lead,
        )
        raise

    # ── (3) Record the CRM-side id ───────────────────────────────────
    if crm_id:
        lead.crm_external_id = crm_id
        lead.save(update_fields=["crm_external_id", "updated_at"])

    CrmSyncEvent.objects.create(
        connection=connection,
        direction="outbound",
        external_event_id=event_id,
        payload={"crm_external_id": crm_id},
        processed=True,
        lead=lead,
    )
    return crm_id


def handle_inbound_event(*, connection: "CrmConnection", payload: dict) -> bool:
    """Apply an inbound CRM webhook. Returns True if processed,
    False if dropped (echo of our own push, or wrong-event-shape)."""
    from crm.models import CrmSyncEvent

    connector = get_connector(connection)
    parsed = connector.parse_inbound_status(payload)
    if parsed is None:
        return False

    # Echo dedup: if any CtwaLead has the same external_event_id
    # stamped, this is our own push coming back.
    from ctwa.models import CtwaLead

    matching_lead = (
        CtwaLead.objects.filter(
            tenant=connection.tenant,
            last_crm_external_event_id=parsed.external_event_id,
        )
        .only("id")
        .first()
    )
    if matching_lead is not None:
        CrmSyncEvent.objects.create(
            connection=connection,
            direction="inbound",
            external_event_id=parsed.external_event_id,
            payload=payload,
            processed=False,
            skip_reason="own_push_echo",
            lead=matching_lead,
        )
        return False

    # Resolve target lead by crm_external_id.
    lead = (
        CtwaLead.objects.filter(tenant=connection.tenant, crm_external_id=parsed.crm_external_id)
        .order_by("-updated_at")
        .first()
    )
    if lead is None:
        CrmSyncEvent.objects.create(
            connection=connection,
            direction="inbound",
            external_event_id=parsed.external_event_id,
            payload=payload,
            processed=False,
            skip_reason="lead_not_found",
        )
        return False

    # Map CRM status → Jina status. Default: keep current status if
    # no explicit mapping exists.
    mapping = connection.mappings.filter(crm_value=parsed.new_status).values_list("jina_value", flat=True).first()
    if mapping:
        lead.qualification_status = mapping
        lead.save(update_fields=["qualification_status", "updated_at"])

    CrmSyncEvent.objects.create(
        connection=connection,
        direction="inbound",
        external_event_id=parsed.external_event_id,
        payload=payload,
        processed=True,
        lead=lead,
    )
    return True


__all__ = [
    "CrmConnector",
    "CrmStatusEvent",
    "get_connector",
    "handle_inbound_event",
    "push_lead_idempotent",
    "register_connector",
]
