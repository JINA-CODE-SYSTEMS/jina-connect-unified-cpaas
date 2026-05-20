"""CtwaCampaign / CtwaLead model tests (#194 + #201 review)."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction


@pytest.mark.django_db
class TestQualificationSignalGuard:
    def test_clean_forbids_first_message(self, tenant, tenant_wa_app):
        """Model-level defence against ``qualification_signal=first_message``.
        The enum already omits it but a raw SQL update or admin form
        could still try. (#201 review Low)"""
        from ctwa.models import CtwaCampaign

        campaign = CtwaCampaign(
            tenant=tenant,
            name="X",
            tenant_wa_app=tenant_wa_app,
            prefilled_message="Hi",
            status="draft",
            qualification_signal="first_message",
        )
        with pytest.raises(ValidationError) as exc:
            campaign.clean()
        assert "first_message" in str(exc.value)

    def test_clean_accepts_flow_node_default(self, tenant, tenant_wa_app):
        from ctwa.models import CtwaCampaign

        campaign = CtwaCampaign(
            tenant=tenant,
            name="X",
            tenant_wa_app=tenant_wa_app,
            prefilled_message="Hi",
            status="draft",
        )  # default qualification_signal=flow_node
        campaign.clean()  # no raise


@pytest.mark.django_db
class TestMetaAdIdUniqueness:
    def test_unique_meta_ad_id_per_tenant(self, tenant, tenant_wa_app):
        """Partial unique on (tenant, meta_ad_id) where meta_ad_id != "".
        Without this, ingestion's ``filter(...).first()`` silently
        picks one of two campaigns sharing the same ad id."""
        from ctwa.models import CtwaCampaign

        CtwaCampaign.objects.create(
            tenant=tenant,
            name="A",
            tenant_wa_app=tenant_wa_app,
            meta_ad_id="ad-shared-1",
            prefilled_message="Hi",
            status="active",
        )
        with transaction.atomic(), pytest.raises(IntegrityError):
            CtwaCampaign.objects.create(
                tenant=tenant,
                name="B",
                tenant_wa_app=tenant_wa_app,
                meta_ad_id="ad-shared-1",
                prefilled_message="Hi",
                status="paused",
            )

    def test_empty_meta_ad_id_does_not_collide(self, tenant, tenant_wa_app):
        """Two draft campaigns (meta_ad_id="") MUST be allowed."""
        from ctwa.models import CtwaCampaign

        a = CtwaCampaign.objects.create(
            tenant=tenant,
            name="A",
            tenant_wa_app=tenant_wa_app,
            meta_ad_id="",
            prefilled_message="hi",
            status="draft",
        )
        b = CtwaCampaign.objects.create(
            tenant=tenant,
            name="B",
            tenant_wa_app=tenant_wa_app,
            meta_ad_id="",
            prefilled_message="hi",
            status="draft",
        )
        assert a.id != b.id

    def test_different_tenants_can_share_ad_id(self, tenant_wa_app):
        """The unique constraint is tenant-scoped. Ad ids can repeat
        across tenants (e.g. when a creative is shared)."""
        from ctwa.models import CtwaCampaign
        from tenants.models import Tenant

        t1 = tenant_wa_app.tenant
        t2 = Tenant.objects.create(name="t2")
        from tenants.models import TenantWAApp

        wa2 = TenantWAApp.objects.create(
            tenant=t2,
            app_name="t2-wa",
            app_id="app-t2",
            app_secret="s",
            wa_number="+14155551111",
        )
        CtwaCampaign.objects.create(
            tenant=t1,
            name="A",
            tenant_wa_app=tenant_wa_app,
            meta_ad_id="ad-x",
            prefilled_message="hi",
            status="active",
        )
        CtwaCampaign.objects.create(
            tenant=t2,
            name="B",
            tenant_wa_app=wa2,
            meta_ad_id="ad-x",
            prefilled_message="hi",
            status="active",
        )  # no raise
