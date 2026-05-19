"""Shared fixtures for ctwa tests (#201 review)."""

from __future__ import annotations

import uuid

import pytest
from django.utils import timezone


@pytest.fixture
def tenant(db):
    from tenants.models import Tenant

    return Tenant.objects.create(name=f"CtwaTenant-{uuid.uuid4().hex[:8]}")


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
        first_name="X",
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
