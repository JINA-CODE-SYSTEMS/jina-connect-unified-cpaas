"""Credential intake for a client-owned META app (#311, part of #305).

Two gaps in one path, plus the validation that makes the difference between a
typo caught at entry and an app that looks configured and silently does nothing.

**The app secret had nowhere to live.** ``X-Hub-Signature-256`` is a symmetric
HMAC-SHA256 keyed on the *sending* app's secret, so whoever verifies a delivery
holds the key that produced it and Meta offers no delegated alternative. The
handover story was ``waba_id`` + ``phone_number_id`` + access token; the app
secret was a fourth item with no field. ``TenantWAApp.meta_app_secret`` is that
field — encrypted at rest following #289, write-only through the API, and
deliberately *not* an entry in ``_BSP_SECRET_FIELDS``: that dict absorbs secrets
older clients already send inside the plaintext ``bsp_credentials`` JSON, and
nobody has ever sent an app secret that way, so an entry would have invented a
plaintext intake route rather than preserved one.

**The META field validation was unreachable.** ``WAAppCreateSerializer`` has
always rejected ``bsp: "META"`` without ``waba_id`` / ``phone_number_id``, and
``WAAppViewSet.get_serializer_class`` never returned it — it chose between the
list, full and safe serializers only. ``POST /wa/v2/apps/`` accepted a META app
with neither identifier, and the failure surfaced much later as sends raising
and webhooks not routing.

**Presence is not correctness.** A presence check catches an empty field, not a
transposed digit. ``wa.services.meta_preflight`` asks META three questions — can
this token read the WABA, is ``phone_number_id`` one of its numbers, is the WABA
subscribed to the app — and turns each failure into a field error rather than a
500.

Out of scope here, on purpose:

* per-app signature verification is #306's second half. ``wa/views.py`` still
  reads one deployment-wide ``settings.META_APP_SECRET`` and is untouched; this
  ticket supplies the column it will read.
* per-app ``hub.verify_token`` checking is #307.

HOW TO RUN:
    DB_NAME=... python -m pytest wa/tests/test_client_credential_intake.py -v
"""

from __future__ import annotations

import itertools
import json
import uuid
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.utils import timezone
from rest_framework.test import APIClient

User = get_user_model()

_mobile_seq = itertools.count(1)


# ─────────────────────────────────────────────────────────────────────────────
# Values under test
#
# Built by helpers rather than assigned to module constants: bandit's B105 fires
# on the *name* of anything holding a string literal when the name reads like a
# credential, and working around that with a blanket skip would also disarm it
# for the cases it is there to catch.
# ─────────────────────────────────────────────────────────────────────────────


def _hmac_key(tag: str) -> str:
    """A stand-in for the client's META app secret."""
    return f"client-app-hmac-{tag}"


def _bearer(tag: str) -> str:
    """A stand-in for a client's META access token."""
    return f"EAAG-client-bearer-{tag}"


def _gupshup_secret() -> str:
    """A stand-in for the *Gupshup* ``app_secret`` — a different credential."""
    return "gupshup-partner-app-secret"


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures and helpers
# ─────────────────────────────────────────────────────────────────────────────


def _tenant():
    from tenants.models import Tenant

    return Tenant.objects.create(name=f"IntakeTenant-{uuid.uuid4().hex[:6]}", is_active=True)


def _client_for(tenant, role_slug: str = "owner"):
    """An ``APIClient`` authenticated as a user holding *role_slug* in *tenant*.

    OWNER by default: ``WAAppViewSet`` only hands the serializer carrying the BSP
    identifiers and credentials to priority >= 80 (#251).
    """
    from tenants.models import TenantRole, TenantUser

    role = TenantRole.objects.get(tenant=tenant, slug=role_slug)
    user = User.objects.create_user(
        username=f"intake_{role_slug}_{uuid.uuid4().hex[:8]}",
        email=f"intake_{uuid.uuid4().hex[:8]}@test.com",
        mobile=f"+9190002{next(_mobile_seq):05d}",
        password="testpass123",
    )
    TenantUser.objects.create(tenant=tenant, user=user, role=role)

    api = APIClient()
    api.force_authenticate(user=user)
    return api


def _client_for_custom_manage_role(tenant):
    """A role below priority 80 that nonetheless holds ``wa_app.manage``.

    Not a configuration the default seed produces — OWNER (100) and ADMIN (80)
    are the only default roles with ``wa_app.manage`` and both clear the #251
    threshold. A tenant can build this by hand, and it is the one case where the
    create path and the read gate disagree, so it is where "wiring the create
    serializer in must widen nothing" is actually testable.
    """
    from tenants.models import RolePermission, TenantRole, TenantUser

    role = TenantRole.objects.create(
        tenant=tenant,
        name="Ops",
        slug=f"ops-{uuid.uuid4().hex[:6]}",
        priority=60,
        is_system=False,
    )
    for permission in ("wa_app.view", "wa_app.manage", "tenant.view"):
        RolePermission.objects.create(role=role, permission=permission, allowed=True)

    user = User.objects.create_user(
        username=f"intake_ops_{uuid.uuid4().hex[:8]}",
        email=f"intake_ops_{uuid.uuid4().hex[:8]}@test.com",
        mobile=f"+9190003{next(_mobile_seq):05d}",
        password="testpass123",
    )
    TenantUser.objects.create(tenant=tenant, user=user, role=role)

    api = APIClient()
    api.force_authenticate(user=user)
    return api


def _wa_app(tenant, **overrides):
    from wa.models import WAApp

    fields = {
        "tenant": tenant,
        "app_name": f"app-{uuid.uuid4().hex[:6]}",
        "app_id": "GUPSHUP-APP-ID",
        "app_secret": _gupshup_secret(),
        "wa_number": f"+1{uuid.uuid4().int % 10**10:010d}",
        "waba_id": f"waba-{uuid.uuid4().hex[:6]}",
        "phone_number_id": f"pn-{uuid.uuid4().hex[:6]}",
        "bsp": "META",
        "is_active": True,
    }
    fields.update(overrides)
    return WAApp.objects.create(**fields)


def _create_payload(tenant, **overrides):
    payload = {
        "tenant": tenant.id,
        "app_name": f"created-{uuid.uuid4().hex[:6]}",
        "phone_number": f"+1{uuid.uuid4().int % 10**10:010d}",
        "app_id": "GUPSHUP-APP-ID",
        "bsp": "META",
        "waba_id": f"waba-{uuid.uuid4().hex[:6]}",
        "phone_number_id": f"pn-{uuid.uuid4().hex[:6]}",
    }
    payload.update(overrides)
    return payload


def _raw_row(pk):
    """Every column of the row, as a database dump would hand it over."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT * FROM tenants_tenantwaapp WHERE id = %s", [pk])
        columns = [c[0] for c in cursor.description]
        row = cursor.fetchone()
    return dict(zip(columns, row))


@contextmanager
def _meta_answering(*, account=None, numbers=None, subscribed=None):
    """Stand in for the three Graph reads the preflight makes.

    Each argument is either a response dict or an ``Exception`` instance to
    raise, which is how ``WAAPI.make_request`` reports every non-2xx.
    """

    def _responder(value):
        def _call(_self):
            if isinstance(value, Exception):
                raise value
            return value

        return _call

    with (
        patch("wa.utility.apis.meta.waba.WABAAPI.get_account_status", _responder(account or {})),
        patch("wa.utility.apis.meta.waba.WABAAPI.get_phone_numbers", _responder(numbers or {})),
        patch("wa.utility.apis.meta.waba.WABAAPI.get_subscribed_apps", _responder(subscribed or {})),
    ):
        yield


def _all_good(app, meta_app_id="meta-app-1"):
    """The three responses a correctly handed-over app produces."""
    return {
        "account": {"id": app.waba_id, "name": "A Business Account"},
        "numbers": {"data": [{"id": app.phone_number_id, "display_phone_number": app.wa_number}]},
        "subscribed": {"data": [{"whatsapp_business_api_data": {"id": meta_app_id}}]},
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1. The app secret has a home
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_the_app_secret_can_be_set_when_the_app_is_created():
    """The gap #306 is blocked on: a fourth handover item with nowhere to go."""
    from wa.models import WAApp

    tenant = _tenant()
    api = _client_for(tenant)

    response = api.post(
        "/wa/v2/apps/",
        _create_payload(tenant, meta_app_secret=_hmac_key("create")),
        format="json",
    )

    assert response.status_code == 201, response.data
    created = WAApp.objects.get(id=response.data["id"])
    assert created.meta_app_secret == _hmac_key("create")


@pytest.mark.django_db
def test_the_app_secret_can_be_replaced_without_deleting_the_app():
    """Acceptance: credentials replaceable without losing the app's history.

    Rotating a secret by deleting and recreating the app would take its messages,
    templates and conversations with it, so the test keeps one of each alongside
    and checks they survive the rotation.
    """
    from contacts.models import TenantContact
    from wa.models import WAApp, WaConversation, WAMessage, WATemplate

    tenant = _tenant()
    app = _wa_app(tenant, meta_app_secret=_hmac_key("old"))
    api = _client_for(tenant)

    template = WATemplate.objects.create(
        tenant=tenant,
        wa_app=app,
        name=f"tpl-{uuid.uuid4().hex[:6]}",
        element_name=f"tpl_{uuid.uuid4().hex[:6]}",
    )
    message = WAMessage.objects.create(wa_app=app, direction="OUTBOUND", text="already sent")
    contact = TenantContact.objects.create(
        tenant=tenant,
        first_name="X",
        phone=f"+1415555{uuid.uuid4().int % 10000:04d}",
    )
    now = timezone.now()
    conversation = WaConversation.objects.create(
        wa_app=app,
        contact=contact,
        first_message_at=now,
        last_inbound_at=now,
        service_window_expires_at=now,
    )

    response = api.patch(
        f"/wa/v2/apps/{app.pk}/",
        {"meta_app_secret": _hmac_key("new")},
        format="json",
    )

    assert response.status_code == 200, response.data
    app.refresh_from_db()
    assert app.meta_app_secret == _hmac_key("new")
    # Same row, same children.
    assert WAApp.objects.filter(pk=app.pk).exists()
    assert WATemplate.objects.filter(pk=template.pk, wa_app=app).exists()
    assert WAMessage.objects.filter(pk=message.pk, wa_app=app).exists()
    assert WaConversation.objects.filter(pk=conversation.pk, wa_app=app).exists()


@pytest.mark.django_db
def test_the_app_secret_never_comes_back_out_of_the_api():
    """Acceptance: never readable through the API.

    Asserted against the bodies the API actually returns rather than against a
    serializer's ``fields`` list, because the leak that mattered in #275 was a
    field that *was* configured write-only and got exposed anyway.
    """
    tenant = _tenant()
    api = _client_for(tenant)

    created = api.post(
        "/wa/v2/apps/",
        _create_payload(tenant, meta_app_secret=_hmac_key("hidden")),
        format="json",
    )
    assert created.status_code == 201, created.data

    # The create response itself, then every other way to read the app.
    bodies = [
        json.dumps(created.data),
        api.get("/wa/v2/apps/").content.decode(),
        api.get(f"/wa/v2/apps/{created.data['id']}/").content.decode(),
        # The legacy endpoint, whose serializer is ``fields = "__all__"`` and so
        # picks up a new model field the moment it is declared.
        api.get("/tenants/tenant-gupshup/").content.decode(),
    ]

    for body in bodies:
        assert _hmac_key("hidden") not in body
        assert "meta_app_secret" not in body


@pytest.mark.django_db
def test_the_app_secret_is_not_recoverable_from_a_raw_select():
    """#289's pattern, followed exactly: encrypted at rest, in every column."""
    tenant = _tenant()
    app = _wa_app(tenant, meta_app_secret=_hmac_key("at-rest"))

    for name, value in _raw_row(app.pk).items():
        assert _hmac_key("at-rest") not in str(value), f"plaintext app secret in column {name}"

    # Still readable through the model, which is the point of encrypting rather
    # than hashing: #306 has to be able to recompute an HMAC with it.
    app.refresh_from_db()
    assert app.meta_app_secret == _hmac_key("at-rest")


@pytest.mark.django_db
def test_a_patch_of_an_unrelated_field_leaves_a_live_app_secret_alone():
    """A rename must not silently disarm the key that verifies webhooks."""
    tenant = _tenant()
    app = _wa_app(tenant, meta_app_secret=_hmac_key("untouched"))
    api = _client_for(tenant)

    response = api.patch(f"/wa/v2/apps/{app.pk}/", {"app_name": "renamed"}, format="json")

    assert response.status_code == 200, response.data
    app.refresh_from_db()
    assert app.app_name == "renamed"
    assert app.meta_app_secret == _hmac_key("untouched")


@pytest.mark.django_db
def test_a_blank_app_secret_is_rejected_rather_than_clearing_a_live_one():
    """An empty string is far more often a form that submitted nothing than a
    deliberate "forget the key that verifies my webhooks"."""
    tenant = _tenant()
    app = _wa_app(tenant, meta_app_secret=_hmac_key("still-here"))
    api = _client_for(tenant)

    response = api.patch(f"/wa/v2/apps/{app.pk}/", {"meta_app_secret": ""}, format="json")

    assert response.status_code == 400, response.data
    assert "meta_app_secret" in response.data
    app.refresh_from_db()
    assert app.meta_app_secret == _hmac_key("still-here")


@pytest.mark.django_db
def test_the_meta_app_secret_and_the_gupshup_app_secret_are_different_credentials():
    """``app_secret`` is the *Gupshup* one and was not repurposed or renamed."""
    tenant = _tenant()
    app = _wa_app(tenant, app_secret=_gupshup_secret(), meta_app_secret=_hmac_key("meta-side"))

    app.refresh_from_db()
    assert app.app_secret == _gupshup_secret()
    assert app.meta_app_secret == _hmac_key("meta-side")
    # And the Gupshup one is still the plaintext column it has always been —
    # changing that is not this ticket, and pretending otherwise would hide it.
    assert _gupshup_secret() in str(_raw_row(app.pk)["app_secret"])


@pytest.mark.django_db
def test_an_app_secret_offered_inside_bsp_credentials_is_refused():
    """``bsp_credentials`` is a plaintext column, and the model only moves the two
    keys older clients already send out of it. An app secret posted there would
    match neither and would simply stay in the clear."""
    from wa.models import WAApp

    tenant = _tenant()
    api = _client_for(tenant)

    response = api.post(
        "/wa/v2/apps/",
        _create_payload(tenant, bsp_credentials={"app_secret": _hmac_key("smuggled")}),
        format="json",
    )

    assert response.status_code == 400, response.data
    assert "bsp_credentials" in response.data
    assert "meta_app_secret" in str(response.data["bsp_credentials"])
    # Nothing was written anywhere, in either column.
    assert not WAApp.objects.filter(tenant=tenant).exists()


@pytest.mark.django_db
def test_the_legacy_endpoint_refuses_the_same_smuggling():
    """Two endpoints write this column. A guard on only one of them would be a
    signpost to the other."""
    tenant = _tenant()
    app = _wa_app(tenant, app_id=f"gs-{uuid.uuid4().hex[:8]}")
    api = _client_for(tenant)

    # That viewset looks an app up by ``app_id``, not by primary key.
    response = api.patch(
        f"/tenants/tenant-gupshup/{app.app_id}/",
        {"bsp_credentials": {"meta_app_secret": _hmac_key("legacy-route")}},
        format="json",
    )

    assert response.status_code == 400, response.data
    app.refresh_from_db()
    assert _hmac_key("legacy-route") not in str(_raw_row(app.pk))


@pytest.mark.django_db
def test_writing_the_app_secret_creates_no_historical_row():
    """Acceptance: the secret never reaches ``simple_history``.

    ``TenantWAApp`` declares no ``HistoricalRecords`` — the one near it in
    ``tenants/models.py`` belongs to ``WABAInfo`` — so there is no historical
    table for a secret to be copied into, and no ``excluded_fields`` to maintain.
    Asserted here rather than assumed, because adding ``HistoricalRecords`` to
    this model later would silently start shadowing every credential on it.
    """
    from wa.models import WAApp

    assert not hasattr(WAApp, "history"), "TenantWAApp has gained history; exclude the secret columns"

    tenant = _tenant()
    _wa_app(tenant, meta_app_secret=_hmac_key("no-history"))

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = %s)",
            ["tenants_historicaltenantwaapp"],
        )
        assert cursor.fetchone()[0] is False


# ─────────────────────────────────────────────────────────────────────────────
# 2. The META field validation runs on the live create path
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_creating_a_meta_app_without_a_waba_id_is_rejected():
    """The heart of #311's second half: this returned 201 and built an app that
    could not send and could not be routed to."""
    from wa.models import WAApp

    tenant = _tenant()
    api = _client_for(tenant)

    payload = _create_payload(tenant)
    payload.pop("waba_id")

    response = api.post("/wa/v2/apps/", payload, format="json")

    assert response.status_code == 400, response.data
    assert "waba_id" in response.data
    assert not WAApp.objects.filter(tenant=tenant).exists()


@pytest.mark.django_db
def test_creating_a_meta_app_without_a_phone_number_id_is_rejected():
    from wa.models import WAApp

    tenant = _tenant()
    api = _client_for(tenant)

    payload = _create_payload(tenant)
    payload.pop("phone_number_id")

    response = api.post("/wa/v2/apps/", payload, format="json")

    assert response.status_code == 400, response.data
    assert "phone_number_id" in response.data
    assert not WAApp.objects.filter(tenant=tenant).exists()


@pytest.mark.django_db
def test_a_meta_app_with_both_identifiers_is_still_created():
    """The rule must not have become "no META app may be created"."""
    tenant = _tenant()
    api = _client_for(tenant)

    response = api.post("/wa/v2/apps/", _create_payload(tenant), format="json")

    assert response.status_code == 201, response.data


@pytest.mark.django_db
def test_a_create_that_names_no_bsp_cannot_slip_past_as_a_meta_app():
    """``bsp`` defaults to META on the model, so an omitted ``bsp`` produces
    exactly the app the validation exists to prevent."""
    from wa.models import WAApp

    tenant = _tenant()
    api = _client_for(tenant)

    payload = _create_payload(tenant)
    payload.pop("bsp")
    payload.pop("waba_id")
    payload.pop("phone_number_id")

    response = api.post("/wa/v2/apps/", payload, format="json")

    assert response.status_code == 400, response.data
    assert not WAApp.objects.filter(tenant=tenant).exists()


@pytest.mark.django_db
def test_a_gupshup_app_needs_no_meta_identifiers():
    """The validation is META's, not everyone's."""
    tenant = _tenant()
    api = _client_for(tenant)

    payload = _create_payload(tenant, bsp="GUPSHUP")
    payload.pop("waba_id")
    payload.pop("phone_number_id")

    response = api.post("/wa/v2/apps/", payload, format="json")

    assert response.status_code == 201, response.data


@pytest.mark.django_db
def test_wiring_the_create_serializer_in_grants_a_lower_role_no_new_field():
    """#251's line, held: a role below priority 80 that can manage apps still
    cannot write the BSP identifiers or the credentials.

    Tested as a consequence — the values are posted and then looked for on the
    saved row — rather than by inspecting a ``fields`` list.
    """
    from wa.models import WAApp

    tenant = _tenant()
    api = _client_for_custom_manage_role(tenant)

    payload = _create_payload(
        tenant,
        bsp="GUPSHUP",
        waba_id="waba-they-should-not-set",
        phone_number_id="pn-they-should-not-set",
        meta_app_secret=_hmac_key("not-theirs"),
        bsp_access_token=_bearer("not-theirs"),
    )

    response = api.post("/wa/v2/apps/", payload, format="json")

    assert response.status_code == 201, response.data
    created = WAApp.objects.get(id=response.data["id"])
    assert created.waba_id != "waba-they-should-not-set"
    assert created.phone_number_id != "pn-they-should-not-set"
    assert created.meta_app_secret == ""
    assert created.bsp_access_token == ""


@pytest.mark.django_db
def test_a_lower_role_cannot_create_a_half_configured_meta_app_either():
    """The two rules meet here: a META app needs identifiers, and this role may
    not set them. The honest answer is to say so — today the create succeeds and
    produces an app that silently sends nothing and receives nothing."""
    from wa.models import WAApp

    tenant = _tenant()
    api = _client_for_custom_manage_role(tenant)

    response = api.post("/wa/v2/apps/", _create_payload(tenant), format="json")

    assert response.status_code == 400, response.data
    assert "bsp" in response.data
    assert not WAApp.objects.filter(tenant=tenant).exists()


@pytest.mark.django_db
def test_a_lower_role_still_cannot_read_the_bsp_identifiers():
    """Regression guard on #251 while the create path is being rewired."""
    tenant = _tenant()
    app = _wa_app(tenant)
    api = _client_for_custom_manage_role(tenant)

    body = api.get(f"/wa/v2/apps/{app.pk}/").content.decode()

    assert app.waba_id not in body
    assert app.phone_number_id not in body


# ─────────────────────────────────────────────────────────────────────────────
# 3. Validated against META, not just for presence
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_the_preflight_passes_when_meta_confirms_every_check():
    tenant = _tenant()
    app = _wa_app(tenant, bsp_access_token=_bearer("good"), meta_app_id="meta-app-1")
    api = _client_for(tenant)

    with _meta_answering(**_all_good(app)):
        response = api.post(f"/wa/v2/apps/{app.pk}/preflight/", {}, format="json")

    assert response.status_code == 200, response.data
    assert response.data["ok"] is True
    assert response.data["token_source"] == "app"
    assert {check["check"] for check in response.data["checks"]} == {
        "credentials",
        "waba_readable",
        "phone_number_listed",
        "app_subscribed",
    }


@pytest.mark.django_db
def test_a_waba_id_the_token_cannot_read_is_a_field_error_on_waba_id():
    """The transposed digit a presence check cannot see. ``make_request`` raises a
    bare ``Exception`` for every non-2xx, so the alternative is a 500."""
    tenant = _tenant()
    app = _wa_app(tenant, bsp_access_token=_bearer("wrong-waba"))
    api = _client_for(tenant)

    with _meta_answering(account=Exception("Request failed with status code 400: Unsupported get request")):
        response = api.post(f"/wa/v2/apps/{app.pk}/preflight/", {}, format="json")

    assert response.status_code == 400, response.data
    assert "waba_id" in response.data
    assert app.waba_id in str(response.data["waba_id"])


@pytest.mark.django_db
def test_a_waba_that_answers_for_a_different_id_is_a_field_error():
    """A 200 is not by itself proof that the id asked for is the id answered."""
    tenant = _tenant()
    app = _wa_app(tenant, bsp_access_token=_bearer("aliased"))
    api = _client_for(tenant)

    with _meta_answering(account={"id": "some-other-waba"}):
        response = api.post(f"/wa/v2/apps/{app.pk}/preflight/", {}, format="json")

    assert response.status_code == 400, response.data
    assert "some-other-waba" in str(response.data["waba_id"])


@pytest.mark.django_db
def test_a_phone_number_id_that_is_not_on_the_waba_is_a_field_error():
    tenant = _tenant()
    app = _wa_app(tenant, bsp_access_token=_bearer("wrong-pn"), meta_app_id="meta-app-1")
    api = _client_for(tenant)

    good = _all_good(app)
    good["numbers"] = {"data": [{"id": "pn-belonging-to-someone-else"}]}

    with _meta_answering(**good):
        response = api.post(f"/wa/v2/apps/{app.pk}/preflight/", {}, format="json")

    assert response.status_code == 400, response.data
    assert "phone_number_id" in response.data
    assert app.phone_number_id in str(response.data["phone_number_id"])
    # The other numbers on a shared WABA belong to another customer.
    assert "pn-belonging-to-someone-else" not in str(response.data["phone_number_id"])


@pytest.mark.django_db
def test_a_waba_subscribed_to_no_app_is_a_field_error():
    """The quiet killer: the callback URL is configured once per app, but every
    WABA must additionally be subscribed to it or META delivers nothing at all,
    with no error anywhere to say so."""
    tenant = _tenant()
    app = _wa_app(tenant, bsp_access_token=_bearer("unsubscribed"), meta_app_id="meta-app-1")
    api = _client_for(tenant)

    good = _all_good(app)
    good["subscribed"] = {"data": []}

    with _meta_answering(**good):
        response = api.post(f"/wa/v2/apps/{app.pk}/preflight/", {}, format="json")

    assert response.status_code == 400, response.data
    assert "meta_app_id" in response.data


@pytest.mark.django_db
def test_a_waba_subscribed_to_a_different_app_is_a_field_error():
    tenant = _tenant()
    app = _wa_app(tenant, bsp_access_token=_bearer("crossed"), meta_app_id="meta-app-ours")
    api = _client_for(tenant)

    good = _all_good(app, meta_app_id="meta-app-theirs")

    with _meta_answering(**good):
        response = api.post(f"/wa/v2/apps/{app.pk}/preflight/", {}, format="json")

    assert response.status_code == 400, response.data
    assert "meta-app-ours" in str(response.data["meta_app_id"])


@pytest.mark.django_db
def test_every_failing_check_is_reported_in_one_run():
    """Two things wrong means two field errors, not a guessing game."""
    tenant = _tenant()
    app = _wa_app(tenant, bsp_access_token=_bearer("two-faults"), meta_app_id="meta-app-1")
    api = _client_for(tenant)

    good = _all_good(app)
    good["numbers"] = {"data": [{"id": "pn-other"}]}
    good["subscribed"] = {"data": []}

    with _meta_answering(**good):
        response = api.post(f"/wa/v2/apps/{app.pk}/preflight/", {}, format="json")

    assert response.status_code == 400, response.data
    assert "phone_number_id" in response.data
    assert "meta_app_id" in response.data
    # And the passing checks come back too: "the number id is wrong" reads very
    # differently from "nothing about this app works".
    passed = {c["check"] for c in response.data["preflight"]["checks"] if c["passed"]}
    assert "waba_readable" in passed


@pytest.mark.django_db
def test_a_graph_outage_is_a_field_error_and_not_a_500():
    """Acceptance: never a 500. Every non-2xx arrives as the same bare
    ``Exception``, so an outage and a typo take the same path out."""
    tenant = _tenant()
    app = _wa_app(tenant, bsp_access_token=_bearer("outage"))
    api = _client_for(tenant)

    with _meta_answering(account=Exception("Request failed with status code 500")):
        response = api.post(f"/wa/v2/apps/{app.pk}/preflight/", {}, format="json")

    assert response.status_code == 400, response.data


@pytest.mark.django_db
def test_the_preflight_never_echoes_the_access_token():
    """Provider error text is formatted from a request that held the bearer
    token, and it is about to become a response body."""
    tenant = _tenant()
    app = _wa_app(tenant, bsp_access_token=_bearer("leaky"))
    api = _client_for(tenant)

    leak = Exception(f"Request failed: Authorization: Bearer {_bearer('leaky')} was rejected")

    with _meta_answering(account=leak):
        response = api.post(f"/wa/v2/apps/{app.pk}/preflight/", {}, format="json")

    assert response.status_code == 400, response.data
    assert _bearer("leaky") not in json.dumps(response.data)


@pytest.mark.django_db
def test_the_preflight_is_rerunnable_without_re_entering_any_credential():
    """Acceptance: re-runnable on demand. The request body is empty both times —
    a token goes stale, a WABA moves, someone unsubscribes the app, and none of
    that is visible locally."""
    tenant = _tenant()
    app = _wa_app(tenant, bsp_access_token=_bearer("stored"), meta_app_id="meta-app-1")
    api = _client_for(tenant)

    with _meta_answering(**_all_good(app)):
        first = api.post(f"/wa/v2/apps/{app.pk}/preflight/", {}, format="json")

    good = _all_good(app)
    good["subscribed"] = {"data": []}
    with _meta_answering(**good):
        second = api.post(f"/wa/v2/apps/{app.pk}/preflight/", {}, format="json")

    assert first.status_code == 200, first.data
    assert second.status_code == 400, second.data


@pytest.mark.django_db
def test_a_failing_preflight_changes_nothing_on_the_app():
    """It is the only write-shaped endpoint here that writes nothing: a failing
    check must never degrade an app that is currently working."""
    tenant = _tenant()
    app = _wa_app(tenant, bsp_access_token=_bearer("intact"), meta_app_id="meta-app-1")
    api = _client_for(tenant)
    before = {
        "waba_id": app.waba_id,
        "phone_number_id": app.phone_number_id,
        "is_active": app.is_active,
        "updated_at": app.updated_at,
        "bsp_access_token": app.bsp_access_token,
    }

    with _meta_answering(account=Exception("Request failed with status code 400")):
        response = api.post(f"/wa/v2/apps/{app.pk}/preflight/", {}, format="json")

    assert response.status_code == 400, response.data
    app.refresh_from_db()
    for name, value in before.items():
        assert getattr(app, name) == value


@pytest.mark.django_db
def test_the_preflight_says_so_instead_of_calling_meta_when_there_is_nothing_to_try():
    """No token and no WABA id is a configuration answer, not a network one."""
    tenant = _tenant()
    app = _wa_app(tenant, waba_id="", bsp_access_token="")
    api = _client_for(tenant)

    with patch("wa.utility.apis.meta.waba.WABAAPI.get_account_status") as graph:
        response = api.post(f"/wa/v2/apps/{app.pk}/preflight/", {}, format="json")

    assert response.status_code == 400, response.data
    assert "waba_id" in response.data
    graph.assert_not_called()


@pytest.mark.django_db
def test_the_preflight_declines_for_an_app_that_is_not_on_meta(settings):
    """Claiming to have verified a Gupshup app by not calling META would be worse
    than declining to check it."""
    tenant = _tenant()
    app = _wa_app(tenant, bsp="GUPSHUP", bsp_access_token=_bearer("gupshup"))
    api = _client_for(tenant)

    with patch("wa.utility.apis.meta.waba.WABAAPI.get_account_status") as graph:
        response = api.post(f"/wa/v2/apps/{app.pk}/preflight/", {}, format="json")

    assert response.status_code == 400, response.data
    assert "bsp" in response.data
    graph.assert_not_called()


@pytest.mark.django_db
def test_the_preflight_is_gated_on_manage_rather_than_view():
    """The report names the WABA and the subscribed app ids — the identifiers
    #251 keeps away from lower roles — and it spends the stored credentials."""
    tenant = _tenant()
    app = _wa_app(tenant, bsp_access_token=_bearer("gated"))

    for slug in ("agent", "viewer", "manager"):
        api = _client_for(tenant, slug)
        response = api.post(f"/wa/v2/apps/{app.pk}/preflight/", {}, format="json")
        assert response.status_code == 403, f"{slug}: {response.status_code}"


@pytest.mark.django_db
def test_the_preflight_reports_a_fallback_to_the_deployment_wide_token(settings):
    """An app with no token of its own still sends today through
    ``META_PERM_TOKEN``. Preflighting with a stricter rule than the send path
    would report a working app as broken — but which credential was checked has
    to be said out loud."""
    settings.META_PERM_TOKEN = _bearer("platform")

    tenant = _tenant()
    app = _wa_app(tenant, bsp_access_token="", meta_app_id="meta-app-1")
    api = _client_for(tenant)

    with _meta_answering(**_all_good(app)):
        response = api.post(f"/wa/v2/apps/{app.pk}/preflight/", {}, format="json")

    assert response.status_code == 200, response.data
    assert response.data["token_source"] == "deployment"


# ── the same checks, on the create and update paths ──────────────────────────


@pytest.mark.django_db
def test_verify_with_meta_turns_a_bad_waba_id_into_a_field_error_at_create():
    """The point of checking at entry: the typo is caught while the person who
    made it is still looking at the form."""
    from wa.models import WAApp

    tenant = _tenant()
    api = _client_for(tenant)

    payload = _create_payload(
        tenant,
        bsp_access_token=_bearer("create-check"),
        verify_with_meta=True,
    )

    with _meta_answering(account=Exception("Request failed with status code 400: Unsupported get request")):
        response = api.post("/wa/v2/apps/", payload, format="json")

    assert response.status_code == 400, response.data
    assert "waba_id" in response.data
    assert not WAApp.objects.filter(tenant=tenant).exists()


@pytest.mark.django_db
def test_verify_with_meta_saves_the_app_when_meta_confirms_it():
    """And the flag itself is not a model field, so it must not reach the row."""
    from wa.models import WAApp

    tenant = _tenant()
    api = _client_for(tenant)

    payload = _create_payload(
        tenant,
        meta_app_id="meta-app-1",
        bsp_access_token=_bearer("create-ok"),
        meta_app_secret=_hmac_key("create-ok"),
        verify_with_meta=True,
    )

    account = {"id": payload["waba_id"], "name": "A Business Account"}
    numbers = {"data": [{"id": payload["phone_number_id"]}]}
    subscribed = {"data": [{"whatsapp_business_api_data": {"id": "meta-app-1"}}]}

    with _meta_answering(account=account, numbers=numbers, subscribed=subscribed):
        response = api.post("/wa/v2/apps/", payload, format="json")

    assert response.status_code == 201, response.data
    created = WAApp.objects.get(id=response.data["id"])
    assert created.meta_app_secret == _hmac_key("create-ok")
    assert created.bsp_access_token == _bearer("create-ok")


@pytest.mark.django_db
def test_a_create_without_the_flag_does_not_call_meta():
    """Opt-in: the check makes the request wait on Graph, so it is asked for."""
    tenant = _tenant()
    api = _client_for(tenant)

    with patch("wa.utility.apis.meta.waba.WABAAPI.get_account_status") as graph:
        response = api.post("/wa/v2/apps/", _create_payload(tenant), format="json")

    assert response.status_code == 201, response.data
    graph.assert_not_called()


@pytest.mark.django_db
def test_verify_with_meta_on_a_patch_checks_the_new_token_against_the_stored_waba():
    """Replacing one credential must not mean re-entering the others."""
    tenant = _tenant()
    app = _wa_app(tenant, bsp_access_token=_bearer("old"), meta_app_id="meta-app-1")
    api = _client_for(tenant)

    seen = {}

    def _account(self):
        seen["token"] = self.token
        seen["waba_id"] = self.waba_id
        return {"id": app.waba_id}

    with (
        patch("wa.utility.apis.meta.waba.WABAAPI.get_account_status", _account),
        patch(
            "wa.utility.apis.meta.waba.WABAAPI.get_phone_numbers",
            lambda self: {"data": [{"id": app.phone_number_id}]},
        ),
        patch(
            "wa.utility.apis.meta.waba.WABAAPI.get_subscribed_apps",
            lambda self: {"data": [{"whatsapp_business_api_data": {"id": "meta-app-1"}}]},
        ),
    ):
        response = api.patch(
            f"/wa/v2/apps/{app.pk}/",
            {"bsp_access_token": _bearer("rotated"), "verify_with_meta": True},
            format="json",
        )

    assert response.status_code == 200, response.data
    # The token being rotated, against the WABA already on the row.
    assert seen["token"] == _bearer("rotated")
    assert seen["waba_id"] == app.waba_id
    app.refresh_from_db()
    assert app.bsp_access_token == _bearer("rotated")


@pytest.mark.django_db
def test_a_failed_verification_on_a_patch_leaves_the_old_credentials_in_place():
    """A rotation that does not work must not half-apply."""
    tenant = _tenant()
    app = _wa_app(tenant, bsp_access_token=_bearer("working"), meta_app_id="meta-app-1")
    api = _client_for(tenant)

    with _meta_answering(account=Exception("Request failed with status code 401")):
        response = api.patch(
            f"/wa/v2/apps/{app.pk}/",
            {"bsp_access_token": _bearer("broken"), "verify_with_meta": True},
            format="json",
        )

    assert response.status_code == 400, response.data
    app.refresh_from_db()
    assert app.bsp_access_token == _bearer("working")


@pytest.mark.django_db
def test_the_verify_flag_is_never_echoed_back_as_app_data():
    tenant = _tenant()
    api = _client_for(tenant)

    payload = _create_payload(tenant, bsp_access_token=_bearer("flag"), verify_with_meta=True)
    account = {"id": payload["waba_id"]}
    numbers = {"data": [{"id": payload["phone_number_id"]}]}
    subscribed = {"data": [{"whatsapp_business_api_data": {"id": "meta-app-1"}}]}

    with _meta_answering(account=account, numbers=numbers, subscribed=subscribed):
        response = api.post("/wa/v2/apps/", payload, format="json")

    assert response.status_code == 201, response.data
    assert "verify_with_meta" not in json.dumps(response.data)
