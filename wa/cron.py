"""
Cron functions for customer module
"""

import logging

from django.core.management import call_command

logger = logging.getLogger(__name__)

#: Apps to sync per run. A ceiling rather than a page size — this is a cron
#: tick, not a backfill, and an unbounded loop over every app on the platform
#: would hold the lock long enough to overlap the next tick.
WABA_SYNC_BATCH = 200


def check_template_status():
    """
    Cron job to check WhatsApp template statuses
    """
    try:
        logger.info("Starting check_template_status cron job")
        call_command("check_template_statuses_cron", "--verbose")
        logger.info("Completed check_template_status cron job successfully")
    except Exception as e:
        logger.error(f"Error in check_template_status cron job: {str(e)}")
        raise


def sync_waba_info():
    """Refresh each active WhatsApp app's WABA state from its provider.

    ``fetch_waba_info`` existed but only ever ran when somebody opened the
    WABA-info endpoint (#267), so the messaging tier — which caps how many
    recipients a broadcast may have — was whatever it happened to be at the
    last time a human looked. A number promoted from TIER_1K to TIER_100K
    stayed capped until someone noticed and clicked.

    Meta raises a tier without warning, so there is nothing to react to;
    polling is the only way the platform learns. Hourly is ample — tier and
    quality move on the order of days — and cheap: one Graph call per app.

    Failures are recorded per app and do not stop the loop. One app with a
    revoked token must not freeze every other app's tier.
    """
    from tenants.models import WABAInfo
    from wa.adapters import get_bsp_adapter
    from wa.models import WAApp

    apps = WAApp.objects.filter(is_active=True).select_related("tenant")[:WABA_SYNC_BATCH]

    synced = 0
    failed = 0

    for wa_app in apps:
        try:
            result = get_bsp_adapter(wa_app).fetch_waba_info()
        except Exception as exc:  # noqa: BLE001 — one app must not stop the sweep
            failed += 1
            logger.warning("[sync_waba_info] app %s raised: %s", wa_app.pk, exc)
            continue

        if not result.success:
            failed += 1
            # Recorded the same way the on-demand endpoint records it, so an
            # operator sees *when* syncing started failing rather than only
            # that the values look stale.
            waba_info, _ = WABAInfo.objects.get_or_create(wa_app=wa_app)
            waba_info.last_sync_error = {
                "status": "error",
                "message": result.error_message or "Unknown error",
                "provider": result.provider,
            }
            waba_info.save(update_fields=["last_sync_error"])
            logger.warning("[sync_waba_info] app %s failed: %s", wa_app.pk, result.error_message)
            continue

        WABAInfo.update_from_adapter_data(wa_app, result.data or {})
        synced += 1

    logger.info("[sync_waba_info] done — synced=%s failed=%s", synced, failed)
    return {"synced": synced, "failed": failed}
