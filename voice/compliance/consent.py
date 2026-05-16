"""Recording-consent gating (#171).

When a tenant flips on ``TenantVoiceApp.recording_requires_consent``,
adapters must check ``recording_allowed(call)`` before turning on call
recording. Returns ``False`` (with a log line) when consent is
required but missing, so the adapter can dial the call without
attaching ``Record`` verbs / ``record_action`` payloads.

For tenants that don't require explicit consent (the default), the
function always returns ``True`` — the gate is opt-in per tenant.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def recording_allowed(call) -> bool:
    """Return ``True`` if the adapter may record this call.

    Lookup order:

      * If ``TenantVoiceApp.recording_requires_consent`` is False (or
        the tenant has no voice app yet) → True.
      * Otherwise require a ``RecordingConsent`` row for
        ``(call.tenant, call.contact)`` with ``consent_given=True``.
      * Calls with no contact (e.g. early-stage inbound before contact
        resolution) → ``False`` when consent is required; the adapter
        is expected to either tag the contact and retry, or skip
        recording entirely.
    """
    from tenants.models import TenantVoiceApp
    from voice.models import RecordingConsent

    try:
        app = TenantVoiceApp.objects.get(tenant_id=call.tenant_id)
    except TenantVoiceApp.DoesNotExist:
        # Tenant has no voice app row yet — no consent policy to enforce.
        return True

    if not app.recording_requires_consent:
        return True

    contact_id = getattr(call, "contact_id", None)
    if contact_id is None:
        logger.info(
            "[voice.compliance.consent] tenant %s requires consent but call %s has no contact; refusing to record",
            call.tenant_id,
            call.id,
        )
        return False

    has_consent = RecordingConsent.objects.filter(
        tenant_id=call.tenant_id,
        contact_id=contact_id,
        consent_given=True,
    ).exists()
    if not has_consent:
        logger.info(
            "[voice.compliance.consent] consent missing for tenant=%s contact=%s; refusing to record call %s",
            call.tenant_id,
            contact_id,
            call.id,
        )
    return has_consent
