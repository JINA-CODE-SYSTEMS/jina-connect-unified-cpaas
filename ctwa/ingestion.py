"""CTWA inbound-referral handler (#194).

Called from ``wa/tasks.py`` after the WA inbound message has been
parsed and the ``WaConversation`` resolved. Creates the ``CtwaLead``
row, links it to the conversation, and on terminal status transitions
also drives the ``attribution/`` CAPI push (via the standard
``CtwaLead.post_save`` signal handler that #197 wires in).

Orphan handling: if ``referral.source_id`` doesn't match any local
``CtwaCampaign.meta_ad_id``, we still create the lead with
``flagged_orphan_campaign=True``. Reasons:

  * Paused / archived ads stay in feed caches for hours.
  * Tenants run ads outside Jina Connect (manual Ads Manager use).
  * Meta ad IDs occasionally arrive before our publish webhook does.

The weekly reconciliation worker (``ctwa.tasks.reconcile_orphans``)
walks orphans and links them via shadow ``CtwaCampaign(status=external)``
rows when the ad id is found on the tenant's connected ad accounts.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from django.utils import timezone

from ctwa.models import CtwaCampaign, CtwaLead

if TYPE_CHECKING:
    from wa.adapters.ctwa_referral import CtwaReferral
    from wa.models import WaConversation

logger = logging.getLogger(__name__)


def handle_inbound_referral(
    *,
    conversation: "WaConversation",
    referral: "CtwaReferral",
) -> Optional[CtwaLead]:
    """Persist a ``CtwaLead`` for an inbound message carrying a CTWA
    referral. Idempotent against double-call for the same conversation
    (it returns the existing lead).

    Returns ``None`` on any failure — the caller treats CTWA ingestion
    as best-effort; the WA inbound row is already saved.
    """
    if conversation is None or referral is None or not referral.source_id:
        return None

    tenant = conversation.wa_app.tenant if conversation.wa_app_id else None
    if tenant is None:
        logger.warning("[ctwa.ingestion] conversation %s has no tenant", conversation.id)
        return None

    existing = (
        CtwaLead.objects.filter(conversation=conversation, meta_ad_id=referral.source_id).order_by("created_at").first()
    )
    if existing:
        return existing

    # Try to resolve a local campaign by ad id. Tenant-scoped to avoid
    # accidentally matching a different tenant's campaign with the same
    # ad id (rare but possible across re-shared assets). Fetch the
    # whole row (not ``.only("id")``) so the caller in ``wa/tasks.py``
    # can read ``campaign.name`` for the auto-tag without a second
    # round-trip. (#201 second review Medium #5)
    campaign = CtwaCampaign.objects.filter(tenant=tenant, meta_ad_id=referral.source_id).first()

    from ctwa.constants import CtwaCampaignStatus

    flagged_orphan = campaign is None or campaign.status in {
        CtwaCampaignStatus.PAUSED.value,
        CtwaCampaignStatus.ARCHIVED.value,
    }

    try:
        lead = CtwaLead.objects.create(
            tenant=tenant,
            contact=conversation.contact,
            conversation=conversation,
            campaign=campaign,
            flagged_orphan_campaign=flagged_orphan,
            meta_ad_id=referral.source_id,
            ctwa_clid=referral.ctwa_clid or "",
            source_url=referral.source_url or "",
            source_type=referral.source_type or "ad",
            headline=referral.headline or "",
            body=referral.body or "",
            media_type=referral.media_type or "",
            media_url=referral.media_url or "",
            thumbnail_url=referral.thumbnail_url or "",
            first_message_at=conversation.last_inbound_at or timezone.now(),
        )
    except Exception as exc:  # noqa: BLE001 — never break inbound ingestion
        logger.exception("[ctwa.ingestion] failed to create CtwaLead: %s", exc)
        return None

    # Stamp the FK on the conversation so the inbox + future inbound
    # messages know they're in a CTWA-attributed thread.
    if conversation.ctwa_lead_id is None:
        conversation.ctwa_lead = lead
        try:
            conversation.save(update_fields=["ctwa_lead", "updated_at"])
        except Exception:  # noqa: BLE001
            pass

    return lead


__all__ = ["handle_inbound_referral"]
