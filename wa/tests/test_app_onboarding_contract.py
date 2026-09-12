"""The documented contract for client-owned Meta app onboarding (#345).

This file exists because of what happened in #310. Its client-facing half was
built against a *stated* contract while the backend was still in flight, and
four things differed when the backend merged: ``app_id`` was really ``wa_app``;
``configured_at`` did not exist at all, so a state machine built on it had
nothing behind it; there was no rotate endpoint, so a rotate button called
nothing; and the endpoint required ``wa_app.manage`` while the screen was gated
on ``wa_app.view``. The last was a live bug — ``wa_app.view`` is granted to all
five default roles and ``wa_app.manage`` only to owner and admin, so manager,
agent and viewer would each have seen the link, clicked it and been refused with
no explanation. Three of the four matched by luck, which is exactly what hides
the rest.

So the operator documentation in ``docs/channels/whatsapp-client-owned-app``
and ``docs/api/wa-apps`` is written from the merged source, and this file pins
it. If a statement in those pages stops being true, a test here fails and names
it.

**Consequences, not structure.** Every assertion below goes through the real
URLs with a real role: it POSTs and reads back, it calls the actions as a
manager and as an owner, it looks for secrets in bodies the API actually
returned. A test that inspects a ``fields`` list would have passed through
#310's ``app_id``/``wa_app`` mismatch, because the name it asserted would have
been the one it read.

What is deliberately *not* asserted here:

* what the webhook receiver does with the secret once stored — #306, covered by
  ``test_per_app_signature_verification.py``;
* that ``verify_token_scope`` equals ``"deployment"``. #307 flips it to
  ``"app"`` and that is not a contract break: the contract is that the field is
  present and is one of the two known values, so a client reads the scope rather
  than assuming one. Pinning today's value would make #307 fail this file for
  doing exactly what it is meant to do.

No network: the three Graph reads the preflight makes are patched at the client
boundary, the same way ``test_client_credential_intake.py`` patches them.

HOW TO RUN:
    DB_NAME=... python -m pytest wa/tests/test_app_onboarding_contract.py -v
"""

from __future__ import annotations

import itertools
import json
import uuid
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

User = get_user_model()

_mobile_seq = itertools.count(1)

#: The two endpoints whose documented contract this file pins, plus create.
APPS_URL = "/wa/v2/apps/"


def _preflight_url(pk) -> str:
    return f"{APPS_URL}{pk}/preflight/"


def _webhook_setup_url(pk) -> str:
    return f"{APPS_URL}{pk}/webhook-setup/"


# ─────────────────────────────────────────────────────────────────────────────
# Stand-ins for the four handover values
#
# Built by helpers rather than module constants: bandit's B105 fires on the
# *name* of anything holding a string literal when the name reads like a
# credential, and a blanket skip would disarm it for the cases it is for.
# Every value is obviously synthetic — these are published docs-adjacent tests.
# ─────────────────────────────────────────────────────────────────────────────


def _bearer(tag: str) -> str:
    """A stand-in for the client's META access token."""
    return f"EAAG-synthetic-bearer-{tag}"


def _hmac_key(tag: str) -> str:
    """A stand-in for the client's META app secret."""
    return f"synthetic-app-hmac-{tag}"


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


def _tenant():
    from tenants.models import Tenant

    return Tenant.objects.create(name=f"ContractTenant-{uuid.uuid4().hex[:6]}", is_active=True)


def _client_for(tenant, role_slug: str = "owner"):
    """An ``APIClient`` authenticated as a user holding *role_slug* in *tenant*."""
    from tenants.models import TenantRole, TenantUser

    role = TenantRole.objects.get(tenant=tenant, slug=role_slug)
    user = User.objects.create_user(
        username=f"contract_{role_slug}_{uuid.uuid4().hex[:8]}",
        email=f"contract_{uuid.uuid4().hex[:8]}@test.com",
        mobile=f"+9190004{next(_mobile_seq):05d}",
        password="testpass123",  # noqa: S106 — throwaway test credential
    )
    TenantUser.objects.create(tenant=tenant, user=user, role=role)

    api = APIClient()
    api.force_authenticate(user=user)
    return api


def _create_payload(tenant, **overrides):
    """The documented create body for a client-owned META app.

    Exactly the field names the documentation tells a form to send, so a rename
    anywhere in this dict fails here — which is #310's ``app_id``/``wa_app``
    mismatch, caught at the only place that can catch it.
    """
    payload = {
        "tenant": tenant.id,
        "app_name": f"contract-{uuid.uuid4().hex[:6]}",
        "phone_number": f"+1{uuid.uuid4().int % 10**10:010d}",
        # Required by the serializer even for META, where it holds no META
        # meaning. Documented as such; asserted by
        # ``test_create_requires_app_id_even_for_a_meta_app``.
        "app_id": "1234567890",
        "bsp": "META",
        "waba_id": "1234567890",
        "phone_number_id": "9876543210",
        "meta_app_id": "1122334455",
        "bsp_access_token": _bearer("create"),
        "meta_app_secret": _hmac_key("create"),
    }
    payload.update(overrides)
    return payload


def _created_app(tenant, api, **overrides):
    """Create one app through the API and return its id."""
    response = api.post(APPS_URL, _create_payload(tenant, **overrides), format="json")
    assert response.status_code == 201, response.data
    return response.data["id"]


@contextmanager
def _meta_answering(*, account=None, numbers=None, subscribed=None):
    """Stand in for the three Graph reads the preflight makes.

    Patched at ``WABAAPI``, which is the boundary ``meta_preflight`` calls
    through — so nothing in this file can reach the network. Each argument is a
    response dict, or an ``Exception`` instance to raise, which is how
    ``WAAPI.make_request`` reports every non-2xx.
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


def _all_good(*, waba_id="1234567890", phone_number_id="9876543210", meta_app_id="1122334455"):
    """What META answers for a correctly handed-over app."""
    return {
        "account": {"id": waba_id, "name": "A Business Account"},
        "numbers": {"data": [{"id": phone_number_id, "display_phone_number": "+10000000000"}]},
        "subscribed": {"data": [{"whatsapp_business_api_data": {"id": meta_app_id}}]},
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1. POST /wa/v2/apps/ — the field names and their write-only-ness
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_create_accepts_every_documented_field_under_its_documented_name():
    """The whole of #310's first mismatch, in one assertion.

    A create with the documented body must succeed *and* the values must land on
    the row under the documented meaning. A field renamed in the serializer makes
    this a 400 (required field missing) or a silently ignored key, and either way
    the read-back below fails.
    """
    from wa.models import WAApp

    tenant = _tenant()
    api = _client_for(tenant, "owner")
    payload = _create_payload(tenant)

    response = api.post(APPS_URL, payload, format="json")

    assert response.status_code == 201, response.data
    app = WAApp.objects.get(pk=response.data["id"])
    assert app.waba_id == payload["waba_id"]
    assert app.phone_number_id == payload["phone_number_id"]
    assert app.meta_app_id == payload["meta_app_id"]
    assert app.app_name == payload["app_name"]
    assert app.wa_number == payload["phone_number"]
    assert app.bsp == "META"
    # The two credentials reached their encrypted columns, under the documented
    # request field names.
    assert app.bsp_access_token == payload["bsp_access_token"]
    assert app.meta_app_secret == payload["meta_app_secret"]


@pytest.mark.django_db
def test_create_requires_app_id_even_for_a_meta_app():
    """Documented trap: ``app_id`` is the *Gupshup* app id and is still required.

    A form that omits it because "this is a META app" gets a 400 on a field whose
    name says nothing about META. Pinned so the documentation's warning cannot
    quietly become wrong in either direction.
    """
    tenant = _tenant()
    api = _client_for(tenant, "owner")
    payload = _create_payload(tenant)
    payload.pop("app_id")

    response = api.post(APPS_URL, payload, format="json")

    assert response.status_code == 400, response.data
    assert "app_id" in response.data


@pytest.mark.django_db
@pytest.mark.parametrize("missing", ["waba_id", "phone_number_id"])
def test_create_refuses_a_meta_app_without_its_identifiers(missing):
    """#311's enforcement, reported on the field it concerns."""
    tenant = _tenant()
    api = _client_for(tenant, "owner")
    payload = _create_payload(tenant)
    payload.pop(missing)

    response = api.post(APPS_URL, payload, format="json")

    assert response.status_code == 400, response.data
    assert missing in response.data, response.data


@pytest.mark.django_db
def test_the_secret_fields_never_appear_in_any_read_response():
    """Acceptance: write-only in both directions.

    Asserted against the bodies the API actually returned — create, list,
    retrieve — rather than against ``write_only`` flags, because the leak that
    mattered in #275 was a field that *was* configured write-only and got
    exposed anyway.
    """
    tenant = _tenant()
    api = _client_for(tenant, "owner")
    app_id = _created_app(
        tenant,
        api,
        bsp_access_token=_bearer("hidden"),
        meta_app_secret=_hmac_key("hidden"),
    )

    bodies = [
        api.get(APPS_URL).content.decode(),
        api.get(f"{APPS_URL}{app_id}/").content.decode(),
        api.get(_webhook_setup_url(app_id)).content.decode(),
    ]
    with _meta_answering(**_all_good()):
        bodies.append(api.post(_preflight_url(app_id)).content.decode())

    for body in bodies:
        assert _bearer("hidden") not in body, body
        assert _hmac_key("hidden") not in body, body
        for key in ("meta_app_secret", "bsp_access_token", "bsp_partner_app_token", "bsp_credentials"):
            assert key not in body, f"{key} appears in a read response: {body}"


@pytest.mark.django_db
def test_the_create_response_does_not_echo_the_secrets_back():
    """The create response is the one body a form definitely renders."""
    tenant = _tenant()
    api = _client_for(tenant, "owner")

    response = api.post(
        APPS_URL,
        _create_payload(tenant, bsp_access_token=_bearer("echo"), meta_app_secret=_hmac_key("echo")),
        format="json",
    )

    assert response.status_code == 201, response.data
    body = json.dumps(response.data)
    assert _bearer("echo") not in body
    assert _hmac_key("echo") not in body
    assert "meta_app_secret" not in response.data
    assert "bsp_access_token" not in response.data


# ─────────────────────────────────────────────────────────────────────────────
# 2. The role branch in ``get_serializer_class``
#
# A form that shows fields the caller's role cannot submit is #310's permission
# bug wearing different clothes, so what each role may send is contract.
# ─────────────────────────────────────────────────────────────────────────────


def _custom_manage_role_client(tenant, priority: int):
    """A custom role at *priority* holding ``wa_app.manage``.

    Not a configuration the default seed produces: only owner (100) and admin
    (80) hold ``wa_app.manage`` by default and both clear the priority-80
    threshold in ``get_serializer_class``. A tenant can build this by hand, and
    it is the only way the permission gate and the serializer branch disagree.
    """
    from tenants.models import RolePermission, TenantRole, TenantUser

    role = TenantRole.objects.create(
        tenant=tenant,
        name=f"Ops{priority}",
        slug=f"ops-{priority}-{uuid.uuid4().hex[:6]}",
        priority=priority,
        is_system=False,
    )
    for permission in ("wa_app.view", "wa_app.manage", "tenant.view"):
        RolePermission.objects.create(role=role, permission=permission, allowed=True)

    user = User.objects.create_user(
        username=f"contract_ops_{uuid.uuid4().hex[:8]}",
        email=f"contract_ops_{uuid.uuid4().hex[:8]}@test.com",
        mobile=f"+9190005{next(_mobile_seq):05d}",
        password="testpass123",  # noqa: S106 — throwaway test credential
    )
    TenantUser.objects.create(tenant=tenant, user=user, role=role)

    api = APIClient()
    api.force_authenticate(user=user)
    return api


@pytest.mark.django_db
def test_a_role_at_priority_80_or_above_may_send_the_bsp_identifiers():
    """The documented boundary, from the privileged side."""
    tenant = _tenant()
    api = _custom_manage_role_client(tenant, priority=80)

    response = api.post(APPS_URL, _create_payload(tenant), format="json")

    assert response.status_code == 201, response.data
    assert response.data["waba_id"] == "1234567890"


@pytest.mark.django_db
def test_a_role_below_priority_80_cannot_create_a_meta_app_at_all():
    """The documented consequence of the role branch.

    ``WAAppSafeCreateSerializer`` carries no BSP identifier fields, and a META
    app requires them, so the two rules meeting means such a role cannot create a
    META app — the error arrives on ``bsp`` and says who can. A form offered to
    this role must not show the identifier fields, which is the whole reason the
    branch is documented.
    """
    tenant = _tenant()
    api = _custom_manage_role_client(tenant, priority=60)

    response = api.post(APPS_URL, _create_payload(tenant), format="json")

    assert response.status_code == 400, response.data
    assert "bsp" in response.data, response.data
    assert "waba_id" not in response.data


@pytest.mark.django_db
def test_a_role_below_priority_80_never_reads_back_the_bsp_identifiers():
    """#251's line, which the create branch was wired in without moving."""
    tenant = _tenant()
    owner = _client_for(tenant, "owner")
    app_id = _created_app(tenant, owner)

    manager = _client_for(tenant, "manager")
    body = manager.get(f"{APPS_URL}{app_id}/")

    assert body.status_code == 200, body.data
    for hidden in ("waba_id", "phone_number_id", "app_id", "meta_app_id"):
        assert hidden not in body.data, f"{hidden} reached a manager: {body.data}"


# ─────────────────────────────────────────────────────────────────────────────
# 3. The permission each endpoint requires
#
# Read out of the permission map and then *exercised*: #310's live bug was a
# screen gated on the permission the endpoint does not require, and only a call
# with a role that holds one and not the other can catch that.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("action", "permission"),
    [
        ("create", "wa_app.manage"),
        ("preflight", "wa_app.manage"),
        ("webhook_setup", "wa_app.manage"),
    ],
)
def test_the_permission_map_declares_what_the_documentation_says(action, permission):
    """The documented permission string, read from the map and not the action name.

    Cheap, and the only assertion here that is structural — kept because it names
    the exact string a client-side gate must check, and the behavioural tests
    below cannot distinguish ``wa_app.manage`` from a hypothetical third
    permission that owner and admin also hold.
    """
    from wa.viewsets.wa_app import WAAppViewSet

    assert WAAppViewSet.required_permissions[action] == permission


@pytest.mark.django_db
@pytest.mark.parametrize("role_slug", ["manager", "agent", "viewer"])
def test_the_three_roles_with_view_but_not_manage_are_refused_all_three_endpoints(role_slug):
    """#310's live bug, pinned.

    ``wa_app.view`` is granted to all five default roles; ``wa_app.manage`` only
    to owner and admin. A screen gated on ``view`` would show these three roles a
    link to each of these endpoints and they would be refused on click. The
    refusal is a 403 with a ``detail`` string — which is what a client should
    surface instead of offering the control.
    """
    tenant = _tenant()
    owner = _client_for(tenant, "owner")
    app_id = _created_app(tenant, owner)

    api = _client_for(tenant, role_slug)

    # The role really does hold wa_app.view — it can read the app.
    assert api.get(f"{APPS_URL}{app_id}/").status_code == 200

    for response in (
        api.post(APPS_URL, _create_payload(tenant), format="json"),
        api.post(_preflight_url(app_id)),
        api.get(_webhook_setup_url(app_id)),
    ):
        assert response.status_code == 403, response.data
        assert "detail" in response.data, response.data


@pytest.mark.django_db
@pytest.mark.parametrize("role_slug", ["owner", "admin"])
def test_owner_and_admin_reach_all_three_endpoints(role_slug):
    """The other half: the two default roles that do hold ``wa_app.manage``."""
    tenant = _tenant()
    api = _client_for(tenant, role_slug)

    app_id = _created_app(tenant, api)
    with _meta_answering(**_all_good()):
        assert api.post(_preflight_url(app_id)).status_code == 200
    assert api.get(_webhook_setup_url(app_id)).status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# 4. The preflight and webhook-setup actions exist, at the documented paths,
#    with the documented methods
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_preflight_is_a_post_and_webhook_setup_is_a_get():
    """#310's missing rotate endpoint, generalised.

    A button wired to a route nobody implemented calls nothing. The method
    matters as much as the path: the wrong verb is a 405, which a form reports as
    an unexplained failure.
    """
    tenant = _tenant()
    api = _client_for(tenant, "owner")
    app_id = _created_app(tenant, api)

    assert api.get(_webhook_setup_url(app_id)).status_code == 200
    with _meta_answering(**_all_good()):
        assert api.post(_preflight_url(app_id)).status_code == 200

    # The wrong verb never succeeds. GET on the POST-only action is a clean 405;
    # POST on the GET-only one is a **403**, not a 405, because DRF resolves
    # permissions before the handler: ``view.action`` is ``None`` for an
    # unmapped method, ``TenantRolePermission`` falls back to the method name
    # ``"post"``, finds no entry for it, and refuses the write rather than
    # letting DRF answer 405. Documented as 403 so a client does not report "you
    # are not allowed" for what is really a wrong-verb bug in its own call.
    assert api.get(_preflight_url(app_id)).status_code == 405
    assert api.post(_webhook_setup_url(app_id)).status_code == 403


@pytest.mark.django_db
def test_preflight_reports_every_check_and_passes_when_meta_agrees():
    """The documented success body: ``ok``, ``token_source``, ``checks``."""
    tenant = _tenant()
    api = _client_for(tenant, "owner")
    app_id = _created_app(tenant, api)

    with _meta_answering(**_all_good()):
        response = api.post(_preflight_url(app_id))

    assert response.status_code == 200, response.data
    assert response.data["ok"] is True
    # The app has its own token, so the report must not claim it validated the
    # deployment-wide one.
    assert response.data["token_source"] == "app"
    names = [check["check"] for check in response.data["checks"]]
    assert names == ["credentials", "waba_readable", "phone_number_listed", "app_subscribed"]
    assert all(check["passed"] for check in response.data["checks"])


@pytest.mark.django_db
def test_a_failing_preflight_check_reports_on_its_own_field():
    """The documented failure shape, per failing check.

    A transposed digit in ``phone_number_id`` must come back as a field error on
    ``phone_number_id`` — the acceptance criterion #345 states — alongside the
    full report under ``preflight`` so a form can tell "one wrong id" from
    "nothing about this app works".
    """
    tenant = _tenant()
    api = _client_for(tenant, "owner")
    app_id = _created_app(tenant, api)

    answers = _all_good()
    # META lists a different number than the one stored.
    answers["numbers"] = {"data": [{"id": "5555555555"}]}
    with _meta_answering(**answers):
        response = api.post(_preflight_url(app_id))

    assert response.status_code == 400, response.data
    assert "phone_number_id" in response.data, response.data
    assert response.data["preflight"]["ok"] is False
    failed = {c["check"]: c for c in response.data["preflight"]["checks"] if not c["passed"]}
    assert set(failed) == {"phone_number_listed"}
    assert failed["phone_number_listed"]["field"] == "phone_number_id"
    # The checks that passed are still reported — that is the distinction.
    assert response.data["preflight"]["checks"][1]["passed"] is True


@pytest.mark.django_db
def test_an_unreadable_waba_reports_on_waba_id_and_does_not_leak_the_token():
    """The documented rule that provider text is scrubbed before it is returned."""
    tenant = _tenant()
    api = _client_for(tenant, "owner")
    app_id = _created_app(tenant, api, bsp_access_token=_bearer("leaky"))

    answers = _all_good()
    answers["account"] = Exception(f"400 Invalid OAuth access token for {_bearer('leaky')}")
    with _meta_answering(**answers):
        response = api.post(_preflight_url(app_id))

    assert response.status_code == 400, response.data
    assert "waba_id" in response.data, response.data
    body = response.content.decode()
    assert _bearer("leaky") not in body
    assert "[redacted]" in body


@pytest.mark.django_db
def test_preflight_declines_a_non_meta_app_on_the_bsp_field():
    """Documented: the checks are Graph-shaped, so a Gupshup app is declined."""
    tenant = _tenant()
    api = _client_for(tenant, "owner")
    app_id = _created_app(tenant, api, bsp="GUPSHUP")

    response = api.post(_preflight_url(app_id))

    assert response.status_code == 400, response.data
    assert "bsp" in response.data, response.data


@pytest.mark.django_db
def test_verify_with_meta_is_write_only_and_turns_a_bad_handover_into_field_errors():
    """The inline preflight on create: same field errors, no row created."""
    from wa.models import WAApp

    tenant = _tenant()
    api = _client_for(tenant, "owner")
    payload = _create_payload(tenant, verify_with_meta=True)

    answers = _all_good(waba_id=payload["waba_id"])
    answers["account"] = Exception("400 Unsupported get request")
    with _meta_answering(**answers):
        response = api.post(APPS_URL, payload, format="json")

    assert response.status_code == 400, response.data
    assert "waba_id" in response.data, response.data
    assert not WAApp.objects.filter(app_name=payload["app_name"]).exists()

    # And it never comes back out of a read.
    with _meta_answering(**_all_good(waba_id=payload["waba_id"])):
        created = api.post(APPS_URL, payload, format="json")
    assert created.status_code == 201, created.data
    assert "verify_with_meta" not in created.data


# ─────────────────────────────────────────────────────────────────────────────
# 5. GET /wa/v2/apps/{id}/webhook-setup/
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_webhook_setup_returns_every_documented_key():
    """The keys a setup screen renders. A rename here is #310's first mismatch."""
    tenant = _tenant()
    api = _client_for(tenant, "owner")
    app_id = _created_app(tenant, api)

    response = api.get(_webhook_setup_url(app_id))

    assert response.status_code == 200, response.data
    assert set(response.data) == {
        "wa_app",
        "bsp",
        "callback_url",
        "identifier_hint",
        "verify_token",
        "verify_token_scope",
        "verify_token_configured",
    }
    assert response.data["wa_app"] == str(app_id)
    assert response.data["bsp"] == "META"


@pytest.mark.django_db
def test_the_callback_url_is_this_app_s_own_per_app_receiver():
    """Documented: the URL carries this app's identifier, and resolves to it.

    Asserted as a consequence — the path is fed back to
    ``webhook_identity.resolve_app`` and must name the same app — rather than by
    string-matching a URL shape, which would pass for any app's URL.
    """
    from wa.models import WAApp
    from wa.services import webhook_identity

    tenant = _tenant()
    api = _client_for(tenant, "owner")
    app_id = _created_app(tenant, api)
    app = WAApp.objects.get(pk=app_id)

    callback_url = api.get(_webhook_setup_url(app_id)).data["callback_url"]

    assert callback_url.endswith("/")
    identifier = callback_url.rstrip("/").rsplit("/", 1)[-1]
    assert webhook_identity.resolve_app(identifier) == app
    # And the full identifier is never what the hint shows.
    hint = api.get(_webhook_setup_url(app_id)).data["identifier_hint"]
    assert hint != app.webhook_identifier
    assert app.webhook_identifier not in hint


@pytest.mark.django_db
def test_verify_token_scope_is_present_and_is_one_of_the_two_known_values():
    """The contract is the *field*, not today's value.

    ``verify_token`` is deployment-scoped today and #307 flips it to ``"app"``.
    A client must read ``verify_token_scope`` rather than assume, so what is
    pinned here is that the key exists and carries one of the two values the
    schema declares. Asserting ``"deployment"`` would make #307 fail this file
    for doing what it is meant to do.
    """
    tenant = _tenant()
    api = _client_for(tenant, "owner")
    app_id = _created_app(tenant, api)

    response = api.get(_webhook_setup_url(app_id))

    assert response.data["verify_token_scope"] in {"deployment", "app"}
    # ``verify_token_configured`` is the honest answer to "is there a token to
    # paste at all", which a deployment that set no token must not fake.
    assert response.data["verify_token_configured"] is bool(response.data["verify_token"])


@pytest.mark.django_db
def test_two_apps_get_different_callback_urls():
    """The property the whole bring-your-own-app route rests on (#305 D-1).

    Two tenants, two apps, two receivers — if these collided, one client's
    deliveries would authenticate against the other's secret.
    """
    first_tenant = _tenant()
    second_tenant = _tenant()
    first = _client_for(first_tenant, "owner")
    second = _client_for(second_tenant, "owner")

    first_url = first.get(_webhook_setup_url(_created_app(first_tenant, first))).data["callback_url"]
    second_url = second.get(_webhook_setup_url(_created_app(second_tenant, second))).data["callback_url"]

    assert first_url != second_url
