"""``_autotag_ctwa_message`` tests (#195 + #201 third review).

Tapan flagged this helper as untested. Covers:

  * Known-campaign lead → tag name uses campaign name.
  * Orphan lead (no campaign) → tag name uses ``meta_ad_id``.
  * Idempotent re-tagging (same call twice) — unique constraint
    prevents duplicate ``MessageTag`` rows.
  * Falls back to ``meta_ad_id`` when campaign exists but its
    ``name`` is empty.
"""

from __future__ import annotations

import uuid

import pytest
from django.utils import timezone


@pytest.fixture
def autotag_fixture(db):
    """Build the (message, lead-with-campaign, tenant, campaign) tuple."""
    from contacts.models import TenantContact
    from ctwa.models import CtwaCampaign, CtwaLead
    from team_inbox.models import MessageEventIds, Messages
    from tenants.models import Tenant, TenantWAApp
    from wa.models import WaConversation
    from wa.services.conversations import SERVICE_WINDOW

    tenant = Tenant.objects.create(name=f"AutotagTenant-{uuid.uuid4().hex[:6]}")
    wa_app = TenantWAApp.objects.create(
        tenant=tenant,
        app_name="t-wa",
        app_id=f"app-{uuid.uuid4().hex[:8]}",
        app_secret="s",
        wa_number=f"+1415555{uuid.uuid4().int % 10000:04d}",
    )
    contact = TenantContact.objects.create(
        tenant=tenant,
        first_name="X",
        phone=f"+1415555{uuid.uuid4().int % 10000:04d}",
    )
    now = timezone.now()
    conv = WaConversation.objects.create(
        wa_app=wa_app,
        contact=contact,
        first_message_at=now,
        last_inbound_at=now,
        service_window_expires_at=now + SERVICE_WINDOW,
    )
    campaign = CtwaCampaign.objects.create(
        tenant=tenant,
        name="Spring Promo",
        tenant_wa_app=wa_app,
        meta_ad_id=f"ad-{uuid.uuid4().hex[:8]}",
        prefilled_message="hi",
        status="active",
    )
    lead = CtwaLead.objects.create(
        tenant=tenant,
        name="L",
        contact=contact,
        conversation=conv,
        campaign=campaign,
        meta_ad_id=campaign.meta_ad_id,
        first_message_at=now,
        qualification_status="new",
    )
    msg_eid = MessageEventIds.objects.create()
    message = Messages.objects.create(
        tenant=tenant,
        message_id=msg_eid,
        content={"type": "text", "body": "hi"},
        direction="INCOMING",
        platform="WHATSAPP",
        author="CONTACT",
        contact=contact,
    )
    return message, lead, tenant, campaign


@pytest.mark.django_db
class TestAutotagCtwaMessage:
    def test_known_campaign_uses_campaign_name(self, autotag_fixture):
        from team_inbox.models import MessageTag
        from wa.tasks import _autotag_ctwa_message

        message, lead, tenant, campaign = autotag_fixture
        _autotag_ctwa_message(message=message, lead=lead, tenant=tenant)

        tags = MessageTag.objects.filter(message=message)
        assert tags.count() == 1
        tag = tags.first().tag
        assert tag.name == f"CTWA: {campaign.name}"
        assert tags.first().auto is True

    def test_orphan_lead_uses_meta_ad_id(self, autotag_fixture):
        from team_inbox.models import MessageTag
        from wa.tasks import _autotag_ctwa_message

        message, lead, tenant, _campaign = autotag_fixture
        # Strip the campaign FK to simulate orphan.
        lead.campaign = None
        lead.flagged_orphan_campaign = True
        lead.save()

        _autotag_ctwa_message(message=message, lead=lead, tenant=tenant)

        tag = MessageTag.objects.filter(message=message).first().tag
        assert tag.name == f"CTWA: orphan {lead.meta_ad_id}"

    def test_idempotent_double_call(self, autotag_fixture):
        from team_inbox.models import MessageTag
        from wa.tasks import _autotag_ctwa_message

        message, lead, tenant, _ = autotag_fixture
        _autotag_ctwa_message(message=message, lead=lead, tenant=tenant)
        _autotag_ctwa_message(message=message, lead=lead, tenant=tenant)
        _autotag_ctwa_message(message=message, lead=lead, tenant=tenant)
        # Unique (message, tag) constraint prevents duplicates.
        assert MessageTag.objects.filter(message=message).count() == 1

    def test_empty_campaign_name_falls_back_to_ad_id(self, autotag_fixture):
        from team_inbox.models import MessageTag
        from wa.tasks import _autotag_ctwa_message

        message, lead, tenant, campaign = autotag_fixture
        campaign.name = ""
        campaign.save()
        # Re-bind the cache (the original instance in lead.campaign is stale).
        lead.refresh_from_db()
        # Note: refresh_from_db doesn't refetch FK-cached relations.
        # ``lead.campaign`` is the cached old object — re-fetch via the FK.
        lead.campaign = campaign  # use the just-modified instance

        _autotag_ctwa_message(message=message, lead=lead, tenant=tenant)

        tag = MessageTag.objects.filter(message=message).first().tag
        assert tag.name == f"CTWA: {lead.meta_ad_id}"
