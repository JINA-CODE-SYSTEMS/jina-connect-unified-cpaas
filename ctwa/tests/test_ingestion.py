"""Ingestion tests for CTWA inbound referral handling (#194 + #201 review)."""

from __future__ import annotations

import pytest

from wa.adapters.ctwa_referral import CtwaReferral


@pytest.fixture
def referral():
    return CtwaReferral(
        source_type="ad",
        source_id="ad-meta-12345",
        source_url="https://example.com/landing",
        headline="Best Offer Ever",
        body="Tap to chat now",
        ctwa_clid="clid-abc-xyz",
    )


@pytest.mark.django_db
class TestHandleInboundReferral:
    def test_creates_lead_for_known_campaign(self, tenant, tenant_wa_app, conversation, referral):
        from ctwa.ingestion import handle_inbound_referral
        from ctwa.models import CtwaCampaign

        campaign = CtwaCampaign.objects.create(
            tenant=tenant,
            name="C",
            tenant_wa_app=tenant_wa_app,
            meta_ad_id=referral.source_id,
            prefilled_message="hi",
            status="active",
        )

        lead = handle_inbound_referral(conversation=conversation, referral=referral)
        assert lead is not None
        assert lead.campaign_id == campaign.id
        assert lead.flagged_orphan_campaign is False
        assert lead.meta_ad_id == referral.source_id
        assert lead.ctwa_clid == referral.ctwa_clid
        # Conversation gets stamped with the lead FK.
        conversation.refresh_from_db()
        assert conversation.ctwa_lead_id == lead.id

    def test_creates_orphan_lead_for_unknown_ad(self, tenant, conversation, referral):
        """Unknown ad id → still create the lead but flag it."""
        from ctwa.ingestion import handle_inbound_referral
        from ctwa.models import CtwaCampaign

        # No matching campaign.
        assert not CtwaCampaign.objects.filter(meta_ad_id=referral.source_id).exists()

        lead = handle_inbound_referral(conversation=conversation, referral=referral)
        assert lead is not None
        assert lead.campaign_id is None
        assert lead.flagged_orphan_campaign is True

    def test_paused_campaign_creates_orphan_path(self, tenant, tenant_wa_app, conversation, referral):
        """Paused / archived campaign → still attribute, but flag as orphan
        so the inbox tile labels it specially."""
        from ctwa.ingestion import handle_inbound_referral
        from ctwa.models import CtwaCampaign

        CtwaCampaign.objects.create(
            tenant=tenant,
            name="C",
            tenant_wa_app=tenant_wa_app,
            meta_ad_id=referral.source_id,
            prefilled_message="hi",
            status="paused",
        )

        lead = handle_inbound_referral(conversation=conversation, referral=referral)
        assert lead is not None
        assert lead.flagged_orphan_campaign is True

    def test_idempotent_double_call(self, tenant, tenant_wa_app, conversation, referral):
        """Calling ``handle_inbound_referral`` twice for the same
        (conversation, ad_id) returns the existing lead — not a second row."""
        from ctwa.ingestion import handle_inbound_referral
        from ctwa.models import CtwaCampaign, CtwaLead

        CtwaCampaign.objects.create(
            tenant=tenant,
            name="C",
            tenant_wa_app=tenant_wa_app,
            meta_ad_id=referral.source_id,
            prefilled_message="hi",
            status="active",
        )

        a = handle_inbound_referral(conversation=conversation, referral=referral)
        b = handle_inbound_referral(conversation=conversation, referral=referral)
        assert a.id == b.id
        assert CtwaLead.objects.filter(conversation=conversation).count() == 1

    def test_missing_source_id_returns_none(self, conversation):
        from ctwa.ingestion import handle_inbound_referral

        bad = CtwaReferral(source_type="ad", source_id="")
        assert handle_inbound_referral(conversation=conversation, referral=bad) is None
