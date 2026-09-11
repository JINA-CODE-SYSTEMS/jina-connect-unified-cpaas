"""One answer to "which provider is this app on" (#265).

The question used to be answered in three places that disagreed. For a WAApp
with a blank ``bsp`` column:

* the adapter factory returned ``MetaDirectAdapter``;
* both send paths treated "not exactly META" as Gupshup and raised
  *"Gupshup credentials missing"*;
* the sync mapper and the META webhook receiver required an exact match and
  refused the app.

So one row submitted templates through META, sent broadcasts through Gupshup,
imported its templates through the Gupshup mapper — which reads ``elementName``
out of a META payload and fails every row as "Template missing name" — and had
its webhooks answered with ``unknown_app``.

These tests pin the blank case down in each of those five paths, and assert the
default is written in one place rather than agreed in several.
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest

from wa.adapters import DEFAULT_BSP, bsp_q, get_bsp_adapter, resolve_bsp
from wa.adapters.base import AdapterResult
from wa.adapters.gupshup import GupshupAdapter
from wa.adapters.meta_direct import MetaDirectAdapter

pytestmark = pytest.mark.django_db


def _app(tenant, bsp):
    """A WAApp whose ``bsp`` column is exactly *bsp* — blank included.

    Written with ``update()`` rather than ``create(bsp=...)`` on purpose: the
    column's default now fills a blank in, and the whole point here is a row
    that really does hold "".
    """
    from wa.models import WAApp

    app = WAApp.objects.create(
        tenant=tenant,
        app_name=f"App {uuid.uuid4().hex[:6]}",
        app_id=f"app_{uuid.uuid4().hex[:8]}",
        app_secret=f"secret_{uuid.uuid4().hex[:8]}",
        wa_number=f"+1{uuid.uuid4().int % 10**10:010d}",
        waba_id=f"waba_{uuid.uuid4().hex[:8]}",
        phone_number_id=f"phone_{uuid.uuid4().hex[:8]}",
        bsp_credentials={"access_token": "tok"},
        is_verified=True,
        is_active=True,
    )
    WAApp.objects.filter(pk=app.pk).update(bsp=bsp)
    app.refresh_from_db()
    return app


@pytest.fixture
def tenant():
    from wa.tests.test_template_api_v2 import create_test_tenant_and_user

    t, _u, _tok = create_test_tenant_and_user(username=f"bsp{uuid.uuid4().hex[:6]}")
    return t


# ── the default is written down once ──────────────────────────────────────


def test_the_column_default_and_the_code_default_agree():
    """If these drift, a row created without an explicit bsp is on one
    provider according to the database and another according to the factory —
    which is the original bug, reintroduced."""
    from wa.models import WAApp

    column_default = WAApp._meta.get_field("bsp").default
    assert column_default == DEFAULT_BSP


def test_a_blank_column_resolves_to_the_default(tenant):
    assert resolve_bsp(_app(tenant, "")) == DEFAULT_BSP


def test_a_null_bsp_resolves_to_the_default():
    """Not via the database: the column is NOT NULL, so a stored row cannot
    hold NULL. ``resolve_bsp`` still handles it because it is also called on
    unsaved instances and on objects built from partial data, where the
    attribute can legitimately be None."""

    class _Unsaved:
        bsp = None

    assert resolve_bsp(_Unsaved()) == DEFAULT_BSP


def test_an_explicit_bsp_is_left_alone(tenant):
    from wa.models import BSPChoices

    assert resolve_bsp(_app(tenant, BSPChoices.GUPSHUP)) == BSPChoices.GUPSHUP


# ── the five paths, all on the blank case ─────────────────────────────────


def test_the_factory_returns_the_meta_adapter(tenant):
    assert isinstance(get_bsp_adapter(_app(tenant, "")), MetaDirectAdapter)


def test_the_meta_webhook_receiver_finds_the_app(tenant):
    """It filtered ``bsp=META`` literally, so a blank-bsp app's webhooks came
    back ``unknown_app`` while every other path served it."""
    from wa.models import BSPChoices, WAApp

    app = _app(tenant, "")
    found = WAApp.objects.filter(bsp_q(BSPChoices.META)).filter(waba_id=app.waba_id).first()
    assert found == app


def test_a_gupshup_app_is_not_swept_up_by_the_meta_filter(tenant):
    """The widened filter must widen in one direction only."""
    from wa.models import BSPChoices, WAApp

    app = _app(tenant, BSPChoices.GUPSHUP)
    assert not WAApp.objects.filter(bsp_q(BSPChoices.META)).filter(pk=app.pk).exists()
    assert WAApp.objects.filter(bsp_q(BSPChoices.GUPSHUP)).filter(pk=app.pk).exists()


def test_the_sync_mapper_is_the_meta_one(tenant):
    """The Gupshup mapper reads ``elementName``; a META payload carries
    ``name``, so the wrong mapper fails every row as "Template missing name".

    Driven through ``sync_templates_from_bsp`` rather than a mapper function,
    because the choice is made inline there — which is exactly why it could
    drift from the factory's answer in the first place."""
    from wa.services.template_sync import sync_templates_from_bsp

    app = _app(tenant, "")
    meta_shaped = {
        "id": "mt-1",
        "name": "hello_world",
        "language": "en",
        "category": "MARKETING",
        "status": "APPROVED",
        "components": [{"type": "BODY", "text": "Hi"}],
    }

    adapter = MagicMock()
    adapter.list_templates.return_value = AdapterResult(
        success=True, provider="meta_direct", data={"templates": [meta_shaped]}
    )

    with patch("wa.services.template_sync.get_bsp_adapter", return_value=adapter):
        report = sync_templates_from_bsp(app, dry_run=True)

    assert report["failed"] == 0, report["errors"]
    assert report["total_from_bsp"] == 1


def test_a_session_send_goes_to_meta_not_gupshup(tenant):
    """The send path's ``else: Gupshup`` raised "Gupshup credentials missing"
    for exactly the app the factory called META."""
    app = _app(tenant, "")
    adapter = get_bsp_adapter(app)

    api = MagicMock()
    api.send_message.return_value = {"messages": [{"id": "wamid.ABC"}]}

    with patch.object(MetaDirectAdapter, "_get_session_message_api", return_value=api):
        result = adapter.send_session_message({"to": "+15551234567", "text": {"body": "hi"}})

    assert result.success, result.error_message
    assert result.data["message_id"] == "wamid.ABC"


def test_a_template_send_goes_to_meta_not_gupshup(tenant):
    app = _app(tenant, "")
    adapter = get_bsp_adapter(app)

    api = MagicMock()
    api.send_template.return_value = {"messages": [{"id": "wamid.XYZ"}]}

    with patch.object(MetaDirectAdapter, "_get_send_template_api", return_value=api):
        result = adapter.send_template({"to": "+15551234567", "template": {"name": "t"}})

    assert result.success, result.error_message
    assert result.data["message_id"] == "wamid.XYZ"


# ── the response shape the two send paths disagreed about ─────────────────


@pytest.mark.parametrize(
    "response,expected_primary,expected_provider",
    [
        ({"messages": [{"id": "wamid.A"}]}, "wamid.A", None),
        ({"messageId": "uuid-1"}, "uuid-1", "uuid-1"),
        ({"gs_id": "gs-1"}, "gs-1", "gs-1"),
        ({"messages": [{"id": "wamid.B"}], "messageId": "uuid-2"}, "wamid.B", "uuid-2"),
    ],
    ids=["cloud-api-only", "gupshup-messageId", "gupshup-gs_id", "both"],
)
def test_gupshup_ids_are_read_whichever_shape_arrives(tenant, response, expected_primary, expected_provider):
    """``broadcast/tasks`` documented Gupshup as returning ``messages[0].id``
    and ``wa/tasks`` as returning ``messageId`` — for the same client. Both
    cannot be right, and whichever was wrong dropped the id silently, which
    disables the duplicate-send guard in #271 because it keys on the id."""
    from wa.models import BSPChoices

    adapter = get_bsp_adapter(_app(tenant, BSPChoices.GUPSHUP))
    api = MagicMock()
    api.send_message.return_value = response

    with patch.object(GupshupAdapter, "_get_session_message_api", return_value=api):
        result = adapter.send_session_message({"to": "+15551234567"})

    assert result.success, result.error_message
    assert result.data["message_id"] == expected_primary
    assert result.data["provider_message_id"] == expected_provider


def test_a_send_that_returns_no_id_is_not_a_success(tenant):
    """A 200 with no id sets the row to SENT with a blank message_id — which
    is precisely the state #271's duplicate guard cannot see."""
    adapter = get_bsp_adapter(_app(tenant, ""))
    api = MagicMock()
    api.send_message.return_value = {"messages": []}

    with patch.object(MetaDirectAdapter, "_get_session_message_api", return_value=api):
        result = adapter.send_session_message({"to": "+15551234567"})

    assert not result.success
    assert "no message id" in result.error_message


def test_a_provider_error_is_reported_not_swallowed(tenant):
    adapter = get_bsp_adapter(_app(tenant, ""))
    api = MagicMock()
    api.send_message.return_value = {"error": {"message": "131047 re-engagement"}}

    with patch.object(MetaDirectAdapter, "_get_session_message_api", return_value=api):
        result = adapter.send_session_message({"to": "+15551234567"})

    assert not result.success
    assert "131047" in result.error_message


# ── the validator that had no caller ──────────────────────────────────────


def test_the_meta_send_validator_now_runs(tenant):
    """``_validate_send_payload`` existed with zero callers repo-wide, so META
    send payloads were never validated — the validators ran for nobody."""
    adapter = get_bsp_adapter(_app(tenant, ""))
    api = MagicMock()

    with patch.object(MetaDirectAdapter, "_get_send_template_api", return_value=api):
        with patch.object(MetaDirectAdapter, "_validate_send_payload") as validate:
            adapter.send_template({"to": "+1"}, template_type="CAROUSEL")

    validate.assert_called_once()
    assert validate.call_args[0][0] == "CAROUSEL"


def test_a_payload_that_fails_send_validation_is_not_sent(tenant):
    adapter = get_bsp_adapter(_app(tenant, ""))
    api = MagicMock()

    with patch.object(MetaDirectAdapter, "_get_send_template_api", return_value=api):
        with patch.object(MetaDirectAdapter, "_validate_send_payload", side_effect=ValueError("bad carousel")):
            result = adapter.send_template({"to": "+1"}, template_type="CAROUSEL")

    assert not result.success
    assert "bad carousel" in result.error_message
    api.send_template.assert_not_called()


# ── both adapters actually implement the interface ────────────────────────


@pytest.mark.parametrize("cls", [MetaDirectAdapter, GupshupAdapter], ids=["meta", "gupshup"])
def test_the_adapter_implements_the_send_interface(cls):
    """``BaseBSPAdapter`` had no send method at all, which is why both send
    paths hand-rolled the branch instead of using the factory."""
    assert not getattr(cls, "__abstractmethods__", set())
    for name in ("send_template", "send_session_message"):
        assert callable(getattr(cls, name))


def test_the_adapter_result_is_the_send_return_type(tenant):
    adapter = get_bsp_adapter(_app(tenant, ""))
    api = MagicMock()
    api.send_message.return_value = {"messages": [{"id": "wamid.Q"}]}

    with patch.object(MetaDirectAdapter, "_get_session_message_api", return_value=api):
        assert isinstance(adapter.send_session_message({"to": "+1"}), AdapterResult)
