"""The host wallet is Gupshup's, and only Gupshup has one (#256).

`HostWalletSerializer` asked the Gupshup partner API for a balance whatever the
app's BSP was, with `app_id`/`app_secret` — the *Gupshup* fields, which on a Meta
Direct app are empty or hold something else. A Meta-only deployment saw a Gupshup
figure in Gupshup's currency, or an error, in its host header.
"""

from unittest.mock import patch

import pytest

from tenants.models import BSPChoices, Tenant, TenantWAApp
from tenants.serializers import HostWalletSerializer


def _app(bsp):
    tenant = Tenant.objects.create(name=f"Wallet-{bsp or 'blank'}")
    return TenantWAApp.objects.create(
        tenant=tenant,
        app_name=f"app-{bsp or 'blank'}",
        app_id="gs_app",
        app_secret="gs_secret",  # noqa: S106 — test fixture
        wa_number="+27115550100",
        bsp=bsp,
        is_active=True,
    )


@pytest.mark.django_db()
def test_a_meta_deployment_is_told_there_is_no_bsp_wallet():
    """Not a zero balance. A zero reads as an empty wallet, not an absent one."""
    _app(BSPChoices.META)

    with patch("tenants.serializers.WalletAPI") as wallet_api:
        result = HostWalletSerializer().get_wallet_balance()

    wallet_api.assert_not_called()
    assert result["gupshup"]["available"] is False
    assert result["gupshup"]["gupshup_wallet"] is None
    assert "META" in result["gupshup"]["reason"]


@pytest.mark.django_db()
def test_a_blank_bsp_is_meta_here_too():
    """A blank column means META (#265), so it must not reach Gupshup either."""
    _app("")

    with patch("tenants.serializers.WalletAPI") as wallet_api:
        result = HostWalletSerializer().get_wallet_balance()

    wallet_api.assert_not_called()
    assert result["gupshup"]["available"] is False


@pytest.mark.django_db()
def test_the_tenant_side_figures_still_come_back_without_a_bsp_wallet():
    """`total_tenants_balance` and `creditors_outstanding` are ours, not the BSP's."""
    _app(BSPChoices.META)

    with patch("tenants.serializers.WalletAPI"):
        result = HostWalletSerializer().get_wallet_balance()

    assert "total_tenants_balance" in result["gupshup"]
    assert "creditors_outstanding" in result["gupshup"]
    assert result["gupshup"]["total_tenant_count"] >= 1


@pytest.mark.django_db()
def test_a_gupshup_deployment_still_reads_its_wallet():
    """The behaviour that must not regress: Gupshup is still asked, and reported."""
    _app(BSPChoices.GUPSHUP)

    with patch("tenants.serializers.WalletAPI") as wallet_api:
        wallet_api.return_value.get_wallet_balance.return_value = {
            "walletResponse": {"currency": "USD", "currentBalance": 12.5, "overDraftLimit": 0}
        }
        result = HostWalletSerializer().get_wallet_balance()

    wallet_api.assert_called_once()
    assert result["gupshup"]["available"] is True
    assert result["gupshup"]["gupshup_wallet"]["current_balance"] == 12.5
