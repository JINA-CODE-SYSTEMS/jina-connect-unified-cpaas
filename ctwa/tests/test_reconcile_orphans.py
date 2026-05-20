"""``reconcile_orphans`` Celery task tests (#194 + #201 third review).

Tapan flagged the task as untested. Covers:

  * Strategy 1 (local DB lookup) resolves an orphan to a matching
    campaign in the same tenant.
  * **Tenant scoping**: a campaign with the same ``meta_ad_id`` in
    a DIFFERENT tenant must NOT resolve a lead from the first tenant.
    (Strict guarantee of Blocker #3.)
  * Archived campaigns are excluded from resolution.
  * Strategy 2 stub is a no-op → orphan stays orphaned.
  * Backlog WARNING fires above threshold.
"""

from __future__ import annotations

import uuid

import pytest
from django.utils import timezone


@pytest.fixture
def make_lead(db):
    """Factory for orphan leads (no campaign FK)."""
    from contacts.models import TenantContact
    from ctwa.models import CtwaLead
    from tenants.models import Tenant, TenantWAApp
    from wa.models import WaConversation
    from wa.services.conversations import SERVICE_WINDOW

    def _make(*, tenant=None, meta_ad_id: str):
        tenant = tenant or Tenant.objects.create(name=f"OrphTenant-{uuid.uuid4().hex[:6]}")
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
        return CtwaLead.objects.create(
            tenant=tenant,
            name="L",
            contact=contact,
            conversation=conv,
            campaign=None,
            flagged_orphan_campaign=True,
            meta_ad_id=meta_ad_id,
            first_message_at=now,
            qualification_status="new",
        )

    return _make


@pytest.mark.django_db
class TestReconcileOrphans:
    def test_strategy1_resolves_local_match(self, make_lead, db):
        from ctwa.models import CtwaCampaign
        from ctwa.tasks import reconcile_orphans
        from tenants.models import TenantWAApp

        lead = make_lead(meta_ad_id="ad-strat1")
        # A matching active campaign appears in the same tenant.
        wa = TenantWAApp.objects.filter(tenant=lead.tenant).first()
        CtwaCampaign.objects.create(
            tenant=lead.tenant,
            name="C",
            tenant_wa_app=wa,
            meta_ad_id="ad-strat1",
            prefilled_message="hi",
            status="active",
        )

        result = reconcile_orphans()
        lead.refresh_from_db()
        assert result["resolved_local"] == 1
        assert result["still_orphan"] == 0
        assert lead.flagged_orphan_campaign is False
        assert lead.campaign is not None
        assert lead.campaign.meta_ad_id == "ad-strat1"

    def test_cross_tenant_campaign_does_NOT_resolve_orphan(self, make_lead, db):
        """**Blocker #3 strict test.** A campaign in tenant B with the
        same ``meta_ad_id`` MUST NOT resolve a lead from tenant A."""
        from ctwa.models import CtwaCampaign, CtwaLead
        from ctwa.tasks import reconcile_orphans
        from tenants.models import Tenant, TenantWAApp

        # Lead lives in tenant A.
        lead_a = make_lead(meta_ad_id="ad-shared")

        # A campaign with the SAME ad id but in tenant B.
        tenant_b = Tenant.objects.create(name=f"OrphB-{uuid.uuid4().hex[:6]}")
        wa_b = TenantWAApp.objects.create(
            tenant=tenant_b,
            app_name="t-b",
            app_id=f"app-{uuid.uuid4().hex[:8]}",
            app_secret="s",
            wa_number=f"+1415555{uuid.uuid4().int % 10000:04d}",
        )
        CtwaCampaign.objects.create(
            tenant=tenant_b,
            name="B",
            tenant_wa_app=wa_b,
            meta_ad_id="ad-shared",
            prefilled_message="hi",
            status="active",
        )

        result = reconcile_orphans()
        lead_a.refresh_from_db()
        # Tenant scoping holds — lead A stays orphaned.
        assert result["resolved_local"] == 0
        assert result["still_orphan"] == 1
        assert lead_a.flagged_orphan_campaign is True
        assert lead_a.campaign is None
        # And tenant B's campaign was unaffected.
        assert CtwaLead.objects.filter(campaign__isnull=False).count() == 0

    def test_archived_campaign_skipped(self, make_lead, db):
        from ctwa.models import CtwaCampaign
        from ctwa.tasks import reconcile_orphans
        from tenants.models import TenantWAApp

        lead = make_lead(meta_ad_id="ad-archived")
        wa = TenantWAApp.objects.filter(tenant=lead.tenant).first()
        CtwaCampaign.objects.create(
            tenant=lead.tenant,
            name="C",
            tenant_wa_app=wa,
            meta_ad_id="ad-archived",
            prefilled_message="hi",
            status="archived",
        )
        result = reconcile_orphans()
        lead.refresh_from_db()
        assert result["resolved_local"] == 0
        assert lead.flagged_orphan_campaign is True

    def test_strategy2_stub_is_noop(self, make_lead, db):
        from ctwa.tasks import reconcile_orphans

        lead = make_lead(meta_ad_id="ad-noresolve")
        # No matching campaign anywhere → strategy 1 misses, strategy 2
        # stub returns False, lead stays orphaned.
        result = reconcile_orphans()
        lead.refresh_from_db()
        assert result["resolved_local"] == 0
        assert result["still_orphan"] == 1
        assert lead.flagged_orphan_campaign is True

    def test_backlog_warning_threshold(self, make_lead, caplog, db):
        """At backlog ≥ threshold a WARNING fires for ops."""
        import logging

        # Threshold defined inline in the task. Generate 101 orphan
        # leads — enough to trip the warning.
        for i in range(101):
            make_lead(meta_ad_id=f"ad-bulk-{i}")

        from ctwa.tasks import reconcile_orphans

        with caplog.at_level(logging.WARNING):
            result = reconcile_orphans(batch_size=10)
        assert result["total_backlog"] >= 100
        warnings = [r for r in caplog.records if "orphan backlog above threshold" in r.message]
        assert warnings, "expected backlog WARNING; none fired"
