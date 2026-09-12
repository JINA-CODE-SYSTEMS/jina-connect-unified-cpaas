"""An ESF-created app is a Gupshup app, and must be recorded as one (#312).

`create_app_for_tenant` builds an app on Gupshup — partner token, Gupshup API
call, Gupshup's own `app_id` and token — but never passed `bsp`. That was
harmless while the column defaulted to GUPSHUP. #265 flipped the default to
META to make a blank column mean one thing everywhere, and this write inherited
the new default silently.

The consequence is not a mislabelled row. The factory hands the app
`MetaDirectAdapter`, so its Gupshup credentials are never used; the Meta token
resolution falls back to the deployment-wide `META_PERM_TOKEN`, so it fails
somewhere confusing rather than cleanly; and the Gupshup webhook
auto-registration signal tests for an exact GUPSHUP match, so it never fires
and the app receives nothing.

These tests assert the consequence rather than the field, because the field is
only interesting for what it makes the rest of the system do.
"""

import uuid
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.django_db


def _create_via_esf():
    """Run the real ESF creation path with only the Gupshup calls stubbed."""
    from tenants.models import Tenant
    from tenants.services.esf_service import ESFService

    tenant = Tenant.objects.create(name=f"T-{uuid.uuid4().hex[:6]}")

    with patch.object(ESFService, "get_partner_token_static", return_value="partner-token"):
        with patch("tenants.services.esf_service.WABAAPI") as api_cls:
            api_cls.return_value.create_new_app.return_value = {
                "app": {"id": f"gs-{uuid.uuid4().hex[:8]}", "token": "gupshup-app-token"}
            }
            ESFService.create_app_for_tenant(tenant_id=tenant.id)

    from tenants.models import TenantWAApp

    return TenantWAApp.objects.get(tenant=tenant)


def test_an_esf_created_app_is_routed_to_gupshup():
    """The consequence that matters: which adapter the factory hands back."""
    from wa.adapters import get_bsp_adapter
    from wa.adapters.gupshup import GupshupAdapter

    wa_app = _create_via_esf()

    assert isinstance(get_bsp_adapter(wa_app), GupshupAdapter), (
        f"ESF creates a Gupshup app, but the factory returned "
        f"{type(get_bsp_adapter(wa_app)).__name__} (bsp={wa_app.bsp!r})"
    )


def test_an_esf_created_app_records_its_provider_explicitly():
    from tenants.models import BSPChoices

    assert _create_via_esf().bsp == BSPChoices.GUPSHUP


def test_the_gupshup_credentials_are_the_ones_that_get_used():
    """Recorded as META, the app would resolve the deployment-wide
    META_PERM_TOKEN instead of the Gupshup credentials sitting on the row —
    failing somewhere confusing rather than at the point of misconfiguration."""
    from wa.adapters import get_bsp_adapter

    wa_app = _create_via_esf()
    adapter = get_bsp_adapter(wa_app)

    assert adapter._resolve_app_id() == wa_app.app_id


def test_the_column_default_is_still_meta():
    """Pins the coupling this bug came from. If the default ever moves back,
    the explicit `bsp=` above becomes redundant rather than load-bearing — and
    whoever changes it should see this test and know why it is stated."""
    from tenants.models import BSPChoices, TenantWAApp

    assert TenantWAApp._meta.get_field("bsp").default == BSPChoices.META
