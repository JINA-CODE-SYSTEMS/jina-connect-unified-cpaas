"""Shared fixtures for the attribution test suite (#201 review)."""

from __future__ import annotations

import uuid

import pytest
from django.utils import timezone


@pytest.fixture
def tenant(db):
    from tenants.models import Tenant

    return Tenant.objects.create(name=f"AttrTenant-{uuid.uuid4().hex[:8]}")


@pytest.fixture
def tenant_wa_app(db, tenant):
    from tenants.models import TenantWAApp

    return TenantWAApp.objects.create(
        tenant=tenant,
        app_name="t-wa",
        app_id=f"app-{uuid.uuid4().hex[:8]}",
        app_secret="secret",
        wa_number=f"+1415555{uuid.uuid4().int % 10000:04d}",
    )


@pytest.fixture
def contact(db, tenant):
    from contacts.models import TenantContact

    return TenantContact.objects.create(
        tenant=tenant,
        first_name="Test",
        phone=f"+1415555{uuid.uuid4().int % 10000:04d}",
    )


@pytest.fixture
def conversation(db, tenant_wa_app, contact):
    from wa.models import WaConversation
    from wa.services.conversations import SERVICE_WINDOW

    now = timezone.now()
    return WaConversation.objects.create(
        wa_app=tenant_wa_app,
        contact=contact,
        first_message_at=now,
        last_inbound_at=now,
        service_window_expires_at=now + SERVICE_WINDOW,
    )


@pytest.fixture
def campaign(db, tenant, tenant_wa_app):
    from ctwa.models import CtwaCampaign

    return CtwaCampaign.objects.create(
        tenant=tenant,
        name="Test CTWA Campaign",
        tenant_wa_app=tenant_wa_app,
        meta_ad_id=f"ad-{uuid.uuid4().hex[:8]}",
        prefilled_message="Hi! I saw your ad.",
        status="active",
    )


@pytest.fixture
def lead(db, tenant, contact, conversation, campaign):
    from ctwa.models import CtwaLead

    return CtwaLead.objects.create(
        tenant=tenant,
        name="lead",
        contact=contact,
        conversation=conversation,
        campaign=campaign,
        meta_ad_id=campaign.meta_ad_id,
        ctwa_clid="clid-abc-123",
        first_message_at=timezone.now(),
        qualification_status="new",
    )
