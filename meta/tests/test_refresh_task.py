"""Meta token-refresh worker tests (#191 + #201 review).

Daily beat must:
  * Probe each connection not refreshed in the last week.
  * Flip ``needs_reauth=True`` on Meta auth-expired errors.
  * Not crash the whole batch when one probe fails non-auth.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone


@pytest.fixture
def tenant(db):
    from tenants.models import Tenant

    return Tenant.objects.create(name=f"MetaTenant-{uuid.uuid4().hex[:8]}")


@pytest.fixture
def fresh_connection(db, tenant):
    from meta.models import MetaBusinessConnection

    return MetaBusinessConnection.objects.create(
        tenant=tenant,
        name="meta-fresh",
        meta_business_id="biz-1",
        meta_ad_account_id=f"act-{uuid.uuid4().hex[:8]}",
        system_user_token="tkn",
        refreshed_at=timezone.now(),
    )


@pytest.fixture
def stale_connection(db, tenant):
    from meta.models import MetaBusinessConnection

    return MetaBusinessConnection.objects.create(
        tenant=tenant,
        name="meta-stale",
        meta_business_id="biz-2",
        meta_ad_account_id=f"act-{uuid.uuid4().hex[:8]}",
        system_user_token="tkn",
        refreshed_at=timezone.now() - timedelta(days=14),
    )


@pytest.mark.django_db
class TestRefreshWorker:
    def test_skips_fresh_connection(self, fresh_connection):
        from meta.tasks import refresh_expiring_connections

        with patch("meta.tasks._probe") as probe:
            result = refresh_expiring_connections()
        assert probe.call_count == 0
        assert result["probed"] == 0

    def test_probes_stale_connection(self, stale_connection):
        from meta.tasks import refresh_expiring_connections

        with patch("meta.tasks._probe") as probe:
            result = refresh_expiring_connections()
        assert probe.call_count == 1
        assert result["probed"] == 1
        stale_connection.refresh_from_db()
        # refreshed_at bumped after successful probe.
        assert stale_connection.refreshed_at >= timezone.now() - timedelta(seconds=5)

    def test_meta_auth_expired_flips_needs_reauth(self, stale_connection):
        from meta.tasks import _MetaAuthExpiredLocal, refresh_expiring_connections

        with patch("meta.tasks._probe", side_effect=_MetaAuthExpiredLocal()):
            result = refresh_expiring_connections()
        assert result["flipped_reauth"] == 1
        stale_connection.refresh_from_db()
        assert stale_connection.needs_reauth is True

    def test_non_auth_error_does_not_crash_batch(self, stale_connection, tenant):
        # Two stale connections; first probe raises RuntimeError, second
        # should still get probed.
        from meta.models import MetaBusinessConnection
        from meta.tasks import refresh_expiring_connections

        MetaBusinessConnection.objects.create(
            tenant=tenant,
            name="meta-other",
            meta_business_id="biz-3",
            meta_ad_account_id=f"act-{uuid.uuid4().hex[:8]}",
            system_user_token="tkn",
            refreshed_at=timezone.now() - timedelta(days=14),
        )

        call_count = {"n": 0}

        def fake_probe(conn):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("transient")
            # second call OK

        with patch("meta.tasks._probe", side_effect=fake_probe):
            result = refresh_expiring_connections()
        assert result["probed"] == 2
        # Neither flipped — the one that raised RuntimeError isn't an auth error.
        assert result["flipped_reauth"] == 0

    def test_revoked_connection_is_skipped(self, stale_connection):
        stale_connection.revoked_at = timezone.now()
        stale_connection.save()

        from meta.tasks import refresh_expiring_connections

        with patch("meta.tasks._probe") as probe:
            result = refresh_expiring_connections()
        assert probe.call_count == 0
        assert result["probed"] == 0

    def test_already_needs_reauth_is_skipped(self, stale_connection):
        stale_connection.needs_reauth = True
        stale_connection.save()

        from meta.tasks import refresh_expiring_connections

        with patch("meta.tasks._probe") as probe:
            refresh_expiring_connections()
        assert probe.call_count == 0
