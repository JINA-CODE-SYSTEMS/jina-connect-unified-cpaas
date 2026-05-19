"""CTWA Celery tasks (#194)."""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task
def reconcile_orphans(batch_size: int = 200) -> dict:
    """Walk ``CtwaLead`` rows with ``flagged_orphan_campaign=True`` and
    attempt to resolve them to a local ``CtwaCampaign``.

    Resolution strategies (in order):

      1. Re-check whether a local campaign with matching ``meta_ad_id``
         appeared since the lead was created (the publish-webhook race).
      2. Call Meta Marketing API for the ad id across the tenant's
         connected ad accounts; on hit, create a shadow campaign
         (``status='external'``) and link.
      3. If neither resolves, leave flagged for tenant review.

    Strategy 2 needs ``meta/`` OAuth tokens and ``ads/`` Marketing API
    client — both shipped in this PR but the live API call is stubbed
    until #190 (Meta app review) approves the relevant scopes. For now
    only strategy 1 actually resolves orphans.

    Scheduled via Celery beat — weekly cadence.
    """
    from ctwa.models import CtwaCampaign, CtwaLead

    resolved_local = 0
    still_orphan = 0

    qs = CtwaLead.objects.filter(flagged_orphan_campaign=True).order_by("created_at")[:batch_size]
    for lead in qs.iterator():
        campaign = (
            CtwaCampaign.objects.filter(tenant=lead.tenant, meta_ad_id=lead.meta_ad_id)
            .exclude(status="archived")
            .only("id", "status")
            .first()
        )
        if campaign is not None:
            lead.campaign = campaign
            lead.flagged_orphan_campaign = False
            lead.save(update_fields=["campaign", "flagged_orphan_campaign", "updated_at"])
            resolved_local += 1
            continue

        # Strategy 2 — Meta Marketing API lookup. Stubbed in this PR.
        # See ads/ adapter once #190 is approved.
        still_orphan += 1

    # Backlog alert. Strategy 2 (Meta Marketing API lookup) is
    # stubbed until #190, so most orphans don't get resolved by this
    # worker today — log a warning so ops can spot a growing backlog.
    # (#201 review)
    BACKLOG_ALERT_THRESHOLD = 100
    total_orphan_backlog = CtwaLead.objects.filter(flagged_orphan_campaign=True).count()
    if total_orphan_backlog >= BACKLOG_ALERT_THRESHOLD:
        logger.warning(
            "[ctwa.tasks.reconcile_orphans] orphan backlog above threshold: "
            "%d leads unresolved (strategy 2 stubbed pending #190)",
            total_orphan_backlog,
        )

    result = {
        "resolved_local": resolved_local,
        "still_orphan": still_orphan,
        "total_backlog": total_orphan_backlog,
    }
    logger.info("[ctwa.tasks.reconcile_orphans] %s", result)
    return result
