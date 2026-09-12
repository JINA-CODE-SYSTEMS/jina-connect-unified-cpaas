"""The messaging tier has to track META on its own (#267).

`fetch_waba_info` existed and worked, but nothing called it on a schedule —
only the WABA-info endpoint, when a human opened it. So the tier that caps
every broadcast's recipient count was whatever it was the last time somebody
looked, and a number META promoted stayed capped at the old tier.

META raises a tier without notifying anyone, so there is no event to react to.
Polling is the only way the platform finds out.
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest

from wa.adapters.base import AdapterResult
from wa.cron import sync_waba_info

pytestmark = pytest.mark.django_db


def _app(tenant, *, is_active=True):
    from wa.models import WAApp

    return WAApp.objects.create(
        tenant=tenant,
        app_name=f"App {uuid.uuid4().hex[:6]}",
        app_id=f"app_{uuid.uuid4().hex[:8]}",
        app_secret=f"secret_{uuid.uuid4().hex[:8]}",
        wa_number=f"+1{uuid.uuid4().int % 10**10:010d}",
        waba_id=f"waba_{uuid.uuid4().hex[:8]}",
        phone_number_id=f"phone_{uuid.uuid4().hex[:8]}",
        bsp="META",
        is_verified=True,
        is_active=is_active,
    )


@pytest.fixture
def tenant():
    from wa.tests.test_template_api_v2 import create_test_tenant_and_user

    t, _u, _tok = create_test_tenant_and_user(username=f"sync{uuid.uuid4().hex[:6]}")
    return t


def _adapter(**data):
    adapter = MagicMock()
    adapter.fetch_waba_info.return_value = AdapterResult(success=True, provider="meta_direct", data=data)
    return adapter


def test_a_tier_promotion_reaches_the_database_without_anyone_clicking(tenant):
    from tenants.models import WABAInfo

    app = _app(tenant)
    WABAInfo.objects.update_or_create(wa_app=app, defaults={"messaging_limit": "TIER_1K"})

    with patch("wa.adapters.get_bsp_adapter", return_value=_adapter(messaging_limit="TIER_100K")):
        report = sync_waba_info()

    assert report == {"synced": 1, "failed": 0}
    assert WABAInfo.objects.get(wa_app=app).messaging_limit == "TIER_100K"


def test_one_failing_app_does_not_stop_the_others(tenant):
    """An app with a revoked token must not freeze every other app's tier."""
    from tenants.models import WABAInfo

    broken = _app(tenant)
    healthy = _app(tenant)

    def _by_app(wa_app):
        if wa_app.pk == broken.pk:
            adapter = MagicMock()
            adapter.fetch_waba_info.return_value = AdapterResult(
                success=False, provider="meta_direct", error_message="Authentication Failed"
            )
            return adapter
        return _adapter(messaging_limit="TIER_10K")

    with patch("wa.adapters.get_bsp_adapter", side_effect=_by_app):
        report = sync_waba_info()

    assert report == {"synced": 1, "failed": 1}
    assert WABAInfo.objects.get(wa_app=healthy).messaging_limit == "TIER_10K"


def test_an_adapter_that_raises_is_caught_per_app(tenant):
    _app(tenant)
    _app(tenant)

    def _boom(wa_app):
        raise RuntimeError("connection reset")

    with patch("wa.adapters.get_bsp_adapter", side_effect=_boom):
        report = sync_waba_info()

    assert report == {"synced": 0, "failed": 2}


def test_a_failure_is_recorded_so_staleness_is_visible(tenant):
    """Without this an operator sees old numbers and no reason for them."""
    from tenants.models import WABAInfo

    app = _app(tenant)
    adapter = MagicMock()
    adapter.fetch_waba_info.return_value = AdapterResult(
        success=False, provider="meta_direct", error_message="Too Many Requests"
    )

    with patch("wa.adapters.get_bsp_adapter", return_value=adapter):
        sync_waba_info()

    error = WABAInfo.objects.get(wa_app=app).last_sync_error
    assert error["status"] == "error"
    assert "Too Many Requests" in error["message"]


def test_inactive_apps_are_skipped(tenant):
    _app(tenant, is_active=False)

    with patch("wa.adapters.get_bsp_adapter", return_value=_adapter(messaging_limit="TIER_100K")) as factory:
        report = sync_waba_info()

    assert report == {"synced": 0, "failed": 0}
    factory.assert_not_called()


def test_the_job_is_actually_scheduled():
    """The function existing is not the fix — `fetch_waba_info` already
    existed. Being *called on a schedule* is the fix, and this deployment runs
    django-crontab rather than celery beat, so CRONJOBS is where that lives."""
    from django.conf import settings

    entries = [job[1] for job in settings.CRONJOBS]
    assert "wa.cron.sync_waba_info" in entries
