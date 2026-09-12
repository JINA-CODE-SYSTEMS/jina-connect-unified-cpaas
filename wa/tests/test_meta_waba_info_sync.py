"""Meta tier and quality sync (#267).

`WABAInfo.messaging_limit` was written by exactly one parser, which reads
Gupshup's camelCase envelope. On Meta Direct it therefore stayed NULL forever,
`QuotaService.tier_limit` fell back to its conservative 50, and that is
enforced as a hard validation error at broadcast creation — so a tenant on a
real TIER_100K number was refused every campaign over 50 recipients.

The sync endpoint also imported Gupshup's partner API directly, meaning a Meta
app was asked of the wrong provider with the wrong credentials.

HOW TO RUN:
    .venv/bin/python -m pytest wa/tests/test_meta_waba_info_sync.py -v
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from tenants.models import WABAInfo
from wa.adapters.meta_direct import MetaDirectAdapter

PN_ID = "pn-1"


def _numbers(**overrides):
    row = {
        "id": PN_ID,
        "display_phone_number": "+27 82 000 0000",
        "verified_name": "Fab Co",
        "quality_rating": "GREEN",
        "messaging_limit_tier": "TIER_100K",
        "throughput": {"level": "HIGH"},
    }
    row.update(overrides)
    return {"data": [row]}


def _wa_app(**overrides):
    from tenants.models import Tenant
    from wa.models import WAApp

    tenant = Tenant.objects.create(name=f"TierTenant-{uuid.uuid4().hex[:6]}", is_active=True)
    fields = {
        "tenant": tenant,
        "app_name": f"app-{uuid.uuid4().hex[:6]}",
        "app_id": "meta-app-1",
        "app_secret": "s",
        "wa_number": f"+1{uuid.uuid4().int % 10**10:010d}",
        "waba_id": "waba-1",
        "phone_number_id": PN_ID,
        "bsp": "META",
        "bsp_credentials": {"access_token": "tok"},
        "is_active": True,
    }
    fields.update(overrides)
    return WAApp.objects.create(**fields)


def _fetch(wa_app, numbers=None, account=None):
    with (
        patch("wa.utility.apis.meta.waba.WABAAPI.get_phone_numbers", return_value=numbers or _numbers()),
        patch("wa.utility.apis.meta.waba.WABAAPI.get_account_status", return_value=account or {}),
    ):
        return MetaDirectAdapter(wa_app).fetch_waba_info()


# ─────────────────────────────────────────────────────────────────────────────
# Reading Meta's own fields
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_the_tier_is_read_from_meta():
    """The heart of #267 — nothing could populate this field before."""
    result = _fetch(_wa_app())

    assert result.success is True
    assert result.data["messaging_limit"] == "TIER_100K"


@pytest.mark.django_db
def test_quality_and_throughput_are_read_too():
    result = _fetch(_wa_app())

    assert result.data["phone_quality"] == "GREEN"
    # Meta nests throughput as {"level": ...}; the model stores the level.
    assert result.data["throughput"] == "HIGH"
    assert result.data["verified_name"] == "Fab Co"


@pytest.mark.django_db
def test_the_phone_fields_are_requested_explicitly():
    """quality_rating and messaging_limit_tier are not in Graph's defaults.

    Without an explicit ``fields`` param the response parses perfectly and
    every value of interest is simply absent — a silent version of this bug.
    """
    wa_app = _wa_app()

    with patch("wa.utility.apis.meta.waba.WABAAPI.make_request", return_value=_numbers()) as req:
        MetaDirectAdapter(wa_app).fetch_waba_info()

    asked = " ".join(str(c.args[0].get("data", {}).get("fields", "")) for c in req.call_args_list)
    assert "quality_rating" in asked
    assert "messaging_limit_tier" in asked
    assert "throughput" in asked


@pytest.mark.django_db
def test_the_right_number_is_selected_on_a_shared_waba():
    numbers = {
        "data": [
            {"id": "other-pn", "quality_rating": "RED", "messaging_limit_tier": "TIER_50"},
            {"id": PN_ID, "quality_rating": "GREEN", "messaging_limit_tier": "TIER_10K"},
        ]
    }
    result = _fetch(_wa_app(), numbers=numbers)

    assert result.data["messaging_limit"] == "TIER_10K"
    assert result.data["phone_quality"] == "GREEN"


@pytest.mark.django_db
def test_an_unmatched_number_on_a_shared_waba_is_refused_not_guessed():
    """Picking the first row would attribute another app's rating to this one."""
    numbers = {
        "data": [{"id": "a", "messaging_limit_tier": "TIER_50"}, {"id": "b", "messaging_limit_tier": "TIER_10K"}]
    }
    result = _fetch(_wa_app(phone_number_id="not-listed"), numbers=numbers)

    assert result.success is False
    assert "not among" in (result.error_message or "")


@pytest.mark.django_db
def test_a_single_number_waba_tolerates_an_unset_phone_number_id():
    result = _fetch(_wa_app(phone_number_id=""))

    assert result.success is True
    assert result.data["messaging_limit"] == "TIER_100K"


# ─────────────────────────────────────────────────────────────────────────────
# Values we do not recognise
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_an_unknown_tier_is_not_stored():
    """A tier the model does not know would read as real while scoring 50.

    Storing it is worse than leaving it unset, because the value looks
    authoritative in the admin and ``get_limit()`` silently ignores it.
    """
    result = _fetch(_wa_app(), numbers=_numbers(messaging_limit_tier="TIER_500K"))

    assert result.success is True
    assert "messaging_limit" not in result.data


@pytest.mark.django_db
def test_an_unknown_quality_or_throughput_is_not_stored():
    result = _fetch(_wa_app(), numbers=_numbers(quality_rating="PURPLE", throughput={"level": "LUDICROUS"}))

    assert "phone_quality" not in result.data
    assert "throughput" not in result.data


@pytest.mark.django_db
def test_a_missing_throughput_object_is_tolerated():
    result = _fetch(_wa_app(), numbers=_numbers(throughput=None))

    assert result.success is True
    assert "throughput" not in result.data


# ─────────────────────────────────────────────────────────────────────────────
# Failure paths
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_no_phone_numbers_is_a_failure():
    result = _fetch(_wa_app(), numbers={"data": []})

    assert result.success is False
    assert "no phone numbers" in (result.error_message or "").lower()


@pytest.mark.django_db
def test_a_missing_waba_id_names_the_unset_field():
    result = _fetch(_wa_app(waba_id=""))

    assert result.success is False
    assert "WABA ID" in (result.error_message or "")


@pytest.mark.django_db
def test_the_account_call_failing_does_not_discard_the_tier():
    """Account review status is additive; losing it must not lose the tier."""
    wa_app = _wa_app()

    with (
        patch("wa.utility.apis.meta.waba.WABAAPI.get_phone_numbers", return_value=_numbers()),
        patch("wa.utility.apis.meta.waba.WABAAPI.get_account_status", side_effect=Exception("boom")),
    ):
        result = MetaDirectAdapter(wa_app).fetch_waba_info()

    assert result.success is True
    assert result.data["messaging_limit"] == "TIER_100K"


# ─────────────────────────────────────────────────────────────────────────────
# Writing it down
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_the_adapter_data_is_persisted():
    wa_app = _wa_app()
    result = _fetch(wa_app)

    info, error = WABAInfo.update_from_adapter_data(wa_app, result.data)

    assert error is None
    info.refresh_from_db()
    assert info.messaging_limit == "TIER_100K"
    assert info.phone_quality == "GREEN"
    assert info.throughput == "HIGH"


@pytest.mark.django_db
def test_absent_keys_do_not_blank_stored_values():
    """Meta reports no docker_status; a Meta sync must not erase Gupshup's."""
    wa_app = _wa_app()
    # A WABAInfo row is created alongside the app, so seed the existing one.
    WABAInfo.objects.update_or_create(wa_app=wa_app, defaults={"docker_status": "LIVE", "messaging_limit": "TIER_250"})

    WABAInfo.update_from_adapter_data(wa_app, {"messaging_limit": "TIER_100K"})

    info = WABAInfo.objects.get(wa_app=wa_app)
    assert info.messaging_limit == "TIER_100K"
    assert info.docker_status == "LIVE"


@pytest.mark.django_db
def test_unrecognised_keys_are_ignored_rather_than_set():
    wa_app = _wa_app()

    info, _ = WABAInfo.update_from_adapter_data(wa_app, {"messaging_limit": "TIER_1K", "is_active": False})

    assert info.messaging_limit == "TIER_1K"
    assert not hasattr(info, "is_active") or info.is_active is not False


# ─────────────────────────────────────────────────────────────────────────────
# The consequence: the quota that was refusing broadcasts
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_a_synced_tier_raises_the_quota_from_fifty():
    """End to end: this is what unblocks a broadcast over 50 recipients."""
    from wa.services.quota_service import QuotaService

    wa_app = _wa_app()
    assert QuotaService(wa_app).tier_limit == 50, "precondition: unsynced apps sit at the fallback"

    WABAInfo.update_from_adapter_data(wa_app, _fetch(wa_app).data)
    wa_app.refresh_from_db()

    assert QuotaService(wa_app).tier_limit == 100000


@pytest.mark.django_db
def test_an_unsynced_tier_says_so_in_the_refusal():
    """An unsynced tier and a real TIER_50 scored the same and read the same."""
    from wa.services.quota_service import QuotaService

    wa_app = _wa_app()
    result = QuotaService(wa_app).validate_broadcast(
        recipient_phones=[f"+2782000{i:04d}" for i in range(60)],
    )

    assert result["is_valid"] is False
    assert "never been synced" in result["error"]


@pytest.mark.django_db
def test_a_real_tier_50_does_not_claim_to_be_unsynced():
    from wa.services.quota_service import QuotaService

    wa_app = _wa_app()
    WABAInfo.update_from_adapter_data(wa_app, {"messaging_limit": "TIER_50"})
    wa_app.refresh_from_db()

    result = QuotaService(wa_app).validate_broadcast(
        recipient_phones=[f"+2782000{i:04d}" for i in range(60)],
    )

    assert result["is_valid"] is False
    assert "never been synced" not in result["error"]
