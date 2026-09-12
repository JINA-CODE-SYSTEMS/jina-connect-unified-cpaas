"""Per-app webhook URL identity (#310).

A webhook receiver cannot choose a verification secret until it knows which app
sent the delivery, and the only identifiers that say so — ``entry[0].id`` and
``metadata.phone_number_id`` — live inside the body, which is exactly what the
signature is supposed to establish the trustworthiness of. Chicken and egg. The
way out is to put the identity in the path::

    POST /wa/v2/webhooks/meta/<webhook_identifier>/

What these tests pin is therefore not "a webhook is ingested" — that already
worked — but the properties the per-app URL exists to provide, and the ones an
implementation of it could plausibly get wrong:

* the **URL** decides which app an event belongs to, in preference to anything
  the body claims, because the body is the sender's unverified word;
* two apps with two URLs receive only their own events;
* an identifier nobody owns is answered **200** with nothing written and a
  counter incremented — 200 because META throttles delivery to the whole
  deployment on non-200s, so one client's stale dashboard entry must not slow
  every other client's events down;
* the identifier never appears **in full** in a log line: it is the whole of the
  authority to address an app's receiver, and log sinks are a wider audience;
* resolution is **one indexed query** whose cost does not grow with the number
  of apps the instance hosts (#305 D-1 targets 10–100);
* the **legacy unsuffixed path behaves exactly as it did**, because it is
  registered in live Meta App Dashboards and an upgrade must not require anyone
  to re-register a URL.

Per-app *signature* verification (#306's second half, needing #311's column) and
per-app *verify-token* validation (#307) are deliberately out of scope: both are
unblocked by this, and the tests below pin that the seam is a seam — the
per-app path still verifies against the deployment-wide secret today.

HOW TO RUN:
    .venv/bin/python -m pytest wa/tests/test_webhook_app_identity.py -v
"""

from __future__ import annotations

import hashlib
import hmac
import itertools
import json
import logging
import uuid
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APIClient

from wa.services import webhook_identity
from wa.views import APP_UNKNOWN_IDENTIFIER, APP_WRONG_BSP, SIG_BAD_HEADER

LEGACY_META_URL = "/wa/v2/webhooks/meta/"
LEGACY_GUPSHUP_URL = "/wa/v2/webhooks/gupshup/"

SECRET = "per-app-identity-test-app-secret"

User = get_user_model()

_mobile_seq = itertools.count(1)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _tenant(prefix: str = "IdentTenant"):
    from tenants.models import Tenant

    return Tenant.objects.create(name=f"{prefix}-{uuid.uuid4().hex[:8]}", is_active=True)


def _wa_app(tenant=None, **overrides):
    from wa.models import WAApp

    fields = {
        "tenant": tenant or _tenant(),
        "app_name": f"app-{uuid.uuid4().hex[:6]}",
        "app_id": f"gs-{uuid.uuid4().hex[:8]}",
        "app_secret": "s",
        "wa_number": f"+1{uuid.uuid4().int % 10**10:010d}",
        "waba_id": f"waba-{uuid.uuid4().hex[:8]}",
        "phone_number_id": f"pn-{uuid.uuid4().hex[:8]}",
        "bsp": "META",
        "is_active": True,
    }
    fields.update(overrides)
    return WAApp.objects.create(**fields)


def _meta_url(wa_app) -> str:
    return reverse("wa:meta-webhook-app", kwargs={"webhook_identifier": wa_app.webhook_identifier})


def _gupshup_url(wa_app) -> str:
    return reverse("wa:gupshup-webhook-app", kwargs={"webhook_identifier": wa_app.webhook_identifier})


def _meta_body(waba_id: str = "waba-body", phone_number_id: str = "pn-body", text: str = "hello") -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": waba_id,
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "metadata": {"phone_number_id": phone_number_id},
                            "messages": [
                                {
                                    "id": f"wamid.{uuid.uuid4().hex[:10]}",
                                    "from": "919000000001",
                                    "timestamp": "1700000000",
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


def _raw(body: dict) -> bytes:
    return json.dumps(body).encode("utf-8")


def _sign(raw: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()


def _post_signed(client, url: str, body: dict, secret: str = SECRET):
    raw = _raw(body)
    return client.post(
        url,
        data=raw,
        content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256=_sign(raw, secret),
    )


class _Collector(logging.Handler):
    """Collects rendered log messages from the webhook views.

    ``caplog`` cannot see them: ``LOGGING`` sets ``propagate: False`` on the
    ``wa`` logger, so its records never reach the handler pytest installs on the
    root logger.
    """

    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record):  # noqa: D102
        self.lines.append(record.getMessage())

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@contextmanager
def _view_logs():
    handler = _Collector()
    view_logger = logging.getLogger("wa.views")
    previous_level = view_logger.level
    view_logger.addHandler(handler)
    view_logger.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        view_logger.removeHandler(handler)
        view_logger.setLevel(previous_level)


@pytest.fixture(autouse=True)
def _no_pipeline_dispatch():
    """Ingestion and routing are what is under test, not the Celery pipeline.

    Patched rather than relying on eager mode: the root ``conftest`` blanks
    ``CELERY_BROKER_URL`` when no broker is reachable and some receivers check
    that *before* queuing, so "the task ran" and "the task was skipped" are not
    distinguishable from here and neither is the subject of this file.
    """
    with patch("wa.signals._dispatch"):
        yield


def _app_queries(captured) -> list[str]:
    """The application's own queries, with the profiler's noise removed.

    ``django-silk`` is installed and active under ``DEBUG`` (which CI sets), and
    it is noisy in three ways that each broke an assertion while this was
    written: it writes the request, the response *body* and every executed SQL
    string into its own tables, so a match on a column name reappears inside
    silk's INSERTs; it runs an ``EXPLAIN`` of each query, so every query looks
    like two; and ``SILKY_MAX_RECORDED_REQUESTS_CHECK_PERCENT`` makes roughly one
    request in ten do extra purge queries, so raw totals are not even stable.
    """
    return [
        q["sql"] for q in captured.captured_queries if "silk_" not in q["sql"] and not q["sql"].startswith("EXPLAIN")
    ]


def _identity_lookups(captured) -> list[str]:
    """The queries that resolved a webhook identifier, and only those.

    Matching on the ``WHERE`` clause rather than the column name: every
    ``SELECT`` on the app table names the column in its select list, so the name
    alone matches any read of the table.
    """
    return [sql for sql in _app_queries(captured) if '"webhook_identifier" =' in sql]


def _api_client_for(tenant, role_slug: str = "owner"):
    """An APIClient authenticated as a user holding *role_slug* in *tenant*."""
    from tenants.models import TenantRole, TenantUser

    role = TenantRole.objects.get(tenant=tenant, slug=role_slug)
    user = User.objects.create_user(
        username=f"ident_{role_slug}_{uuid.uuid4().hex[:8]}",
        email=f"ident_{uuid.uuid4().hex[:8]}@test.com",
        mobile=f"+9190001{next(_mobile_seq):05d}",
        password="testpass123",
    )
    TenantUser.objects.create(tenant=tenant, user=user, role=role)

    client = APIClient()
    client.force_authenticate(user=user)
    return client


# ─────────────────────────────────────────────────────────────────────────────
# The URL decides which app an event belongs to
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_a_delivery_is_attributed_from_the_url_not_from_the_body(client, settings):
    """The heart of #310.

    The body names *another* app's WABA and number. Nothing in it may move the
    event: the URL is the identity, the body is the sender's unverified claim.
    Were the body consulted first, the per-app URL would buy nothing.
    """
    from wa.models import WAWebhookEvent

    settings.META_APP_SECRET = SECRET
    mine = _wa_app()
    theirs = _wa_app()

    response = _post_signed(
        client,
        _meta_url(mine),
        _meta_body(waba_id=theirs.waba_id, phone_number_id=theirs.phone_number_id),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "received"
    assert WAWebhookEvent.objects.filter(wa_app=mine).count() == 1
    assert not WAWebhookEvent.objects.filter(wa_app=theirs).exists()


@pytest.mark.django_db
def test_two_apps_with_two_urls_each_receive_only_their_own_events(client, settings):
    """Acceptance: two apps, two identifiers, no crossing over."""
    from wa.models import WAWebhookEvent

    settings.META_APP_SECRET = SECRET
    first = _wa_app()
    second = _wa_app()

    _post_signed(client, _meta_url(first), _meta_body(text="for-first"))
    _post_signed(client, _meta_url(second), _meta_body(text="for-second"))
    _post_signed(client, _meta_url(second), _meta_body(text="for-second-again"))

    assert WAWebhookEvent.objects.filter(wa_app=first).count() == 1
    assert WAWebhookEvent.objects.filter(wa_app=second).count() == 2

    assert first.webhook_identifier != second.webhook_identifier


@pytest.mark.django_db
def test_a_body_with_no_identifiers_at_all_still_routes_on_a_per_app_url(client, settings):
    """An account-level update carries no ``phone_number_id``, and on the legacy
    path a body whose WABA matches several apps cannot be attributed at all
    (#309). On a per-app URL there is nothing to disambiguate."""
    from wa.models import WAWebhookEvent

    settings.META_APP_SECRET = SECRET
    app = _wa_app()

    body = {
        "object": "whatsapp_business_account",
        "entry": [{"id": "some-other-waba", "changes": [{"field": "account_update", "value": {}}]}],
    }
    response = _post_signed(client, _meta_url(app), body)

    assert response.json()["status"] == "received"
    event = WAWebhookEvent.objects.get(wa_app=app)
    assert event.event_type == "ACCOUNT"
    assert event.error_message in (None, "")


@pytest.mark.django_db
def test_the_url_shape_is_the_one_a_client_is_told_to_register(client, settings):
    """The literal path matters: it is pasted into a Meta App Dashboard, and a
    reverse() that quietly changed shape would invalidate every registered URL."""
    app = _wa_app()

    assert _meta_url(app) == f"/wa/v2/webhooks/meta/{app.webhook_identifier}/"
    assert _gupshup_url(app) == f"/wa/v2/webhooks/gupshup/{app.webhook_identifier}/"


# ─────────────────────────────────────────────────────────────────────────────
# An identifier nobody owns
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_an_unknown_identifier_is_answered_200_and_writes_nothing(client, settings):
    """Acceptance, and the 200 is the part that must not be "tidied" later: a
    non-200 makes META throttle delivery to the whole deployment."""
    from wa.models import WAWebhookEvent

    settings.META_APP_SECRET = SECRET
    _wa_app()  # a real app exists; this delivery is not for it

    unknown = "whk_" + "x" * 32
    response = client.post(
        f"/wa/v2/webhooks/meta/{unknown}/",
        data=_raw(_meta_body()),
        content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256=_sign(_raw(_meta_body())),
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "reason": APP_UNKNOWN_IDENTIFIER}
    assert not WAWebhookEvent.objects.exists()


@pytest.mark.django_db
def test_an_unknown_identifier_is_counted(client, settings):
    """Acceptance: counted, so a scan or a stale dashboard entry is visible.

    A delta rather than an absolute: the counter is a shared Redis key bucketed
    by UTC day, and this suite must not ``cache.clear()`` — under django_redis
    that is a FLUSHDB of every key in the cache database, including other
    processes' work.
    """
    from tenants.models import BSPChoices

    settings.META_APP_SECRET = SECRET
    before = webhook_identity.unknown_identifier_rejections(BSPChoices.META)

    for suffix in ("a", "b"):
        client.post(
            f"/wa/v2/webhooks/meta/whk_{suffix * 32}/",
            data=_raw(_meta_body()),
            content_type="application/json",
        )

    after = webhook_identity.unknown_identifier_rejections(BSPChoices.META)
    assert after - before == 2


@pytest.mark.django_db
def test_an_unknown_identifier_is_refused_before_the_signature_is_checked(client, settings):
    """Ordering, not just outcome.

    Resolution has to come first — that is the whole reason the identifier is in
    the URL — so a delivery to an unowned identifier is refused as *unknown*
    even when it carries no signature at all, rather than being refused as
    unsigned and leaving the identifier unexamined.
    """
    settings.META_APP_SECRET = SECRET

    response = client.post(
        "/wa/v2/webhooks/meta/whk_" + "z" * 32 + "/",
        data=_raw(_meta_body()),
        content_type="application/json",
    )

    assert response.json()["reason"] == APP_UNKNOWN_IDENTIFIER


@pytest.mark.django_db
@pytest.mark.parametrize(
    "junk",
    [
        "short",
        "whk_" + "x" * 200,
        "whk_has.a.dot",
        "whk_has%20space",
    ],
)
def test_a_malformed_identifier_costs_no_database_query(client, settings, junk):
    """A public URL shape attracts scanners. Shape is checked before the
    database is, so a scan is answered out of nothing."""
    settings.META_APP_SECRET = SECRET
    _wa_app()

    with CaptureQueriesContext(connection) as captured:
        response = client.post(
            f"/wa/v2/webhooks/meta/{junk}/",
            data=_raw(_meta_body()),
            content_type="application/json",
        )

    assert response.status_code in (200, 404)
    assert _identity_lookups(captured) == []


@pytest.mark.django_db
def test_an_identifier_belonging_to_another_bsp_gets_its_own_reason(client, settings):
    """A client pasting their Meta URL into Gupshup's settings (or the reverse)
    is a different mistake from a stale URL, and has a different fix."""
    from wa.models import WAWebhookEvent

    settings.META_APP_SECRET = SECRET
    gupshup_app = _wa_app(bsp="GUPSHUP")

    response = _post_signed(client, _meta_url(gupshup_app), _meta_body())

    assert response.status_code == 200
    assert response.json()["reason"] == APP_WRONG_BSP
    assert not WAWebhookEvent.objects.exists()


# ─────────────────────────────────────────────────────────────────────────────
# The identifier stays out of the logs
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_a_successful_delivery_never_logs_the_identifier_in_full(client, settings):
    """Acceptance. The identifier *is* the authority to address the receiver, so
    a log line carrying it has published a callback URL to every log sink."""
    settings.META_APP_SECRET = SECRET
    app = _wa_app()

    with _view_logs() as logs:
        assert _post_signed(client, _meta_url(app), _meta_body()).json()["status"] == "received"

    assert app.webhook_identifier not in logs.text
    # A hint is logged, or the log line could not be tied to anything at all.
    assert app.webhook_identifier[:12] in logs.text


@pytest.mark.django_db
def test_a_rejected_delivery_never_logs_the_identifier_in_full(client, settings):
    """The rejection path is the one an attacker controls, so it is the one most
    likely to write an attacker-chosen string into the logs in full."""
    settings.META_APP_SECRET = SECRET
    unknown = "whk_" + "q" * 32

    with _view_logs() as logs:
        client.post(
            f"/wa/v2/webhooks/meta/{unknown}/",
            data=_raw(_meta_body()),
            content_type="application/json",
        )

    assert unknown not in logs.text
    assert APP_UNKNOWN_IDENTIFIER in logs.text


@pytest.mark.django_db
def test_the_mask_keeps_a_prefix_and_drops_the_rest():
    """The masking rule itself, so a "make the logs more useful" change cannot
    quietly widen it back to the whole value."""
    app = _wa_app()

    hint = webhook_identity.mask(app.webhook_identifier)

    assert hint != app.webhook_identifier
    assert app.webhook_identifier.startswith(hint.rstrip("…"))
    assert len(hint.rstrip("…")) <= 12
    assert webhook_identity.mask("") == ""
    assert webhook_identity.mask(None) == ""


# ─────────────────────────────────────────────────────────────────────────────
# One indexed query, whatever the instance hosts
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_resolution_is_a_single_indexed_query(client, settings):
    settings.META_APP_SECRET = SECRET
    app = _wa_app()

    with CaptureQueriesContext(connection) as captured:
        _post_signed(client, _meta_url(app), _meta_body())

    identity_queries = _identity_lookups(captured)
    assert len(identity_queries) == 1, identity_queries


@pytest.mark.django_db
def test_resolution_cost_does_not_grow_with_the_number_of_apps(client, settings):
    """Acceptance, and the reason the identifier is in the URL rather than being
    found by trying every candidate app's secret against the body: that is
    O(apps) HMACs over a full request body, per delivery (#305 D-1).
    """
    settings.META_APP_SECRET = SECRET

    alone = _wa_app()
    with CaptureQueriesContext(connection) as small:
        _post_signed(client, _meta_url(alone), _meta_body())

    tenant = _tenant("Crowd")
    crowded = _wa_app(tenant)
    for _ in range(30):
        _wa_app(tenant)

    with CaptureQueriesContext(connection) as large:
        _post_signed(client, _meta_url(crowded), _meta_body())

    assert len(_identity_lookups(small)) == len(_identity_lookups(large)) == 1
    assert len(_app_queries(large)) == len(_app_queries(small)), (
        f"{len(_app_queries(small))} queries with 1 app, {len(_app_queries(large))} with 32 — resolution must not scan"
    )


# ─────────────────────────────────────────────────────────────────────────────
# The seam: per-app secrets and verify tokens are NOT implemented here
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_the_per_app_path_still_verifies_the_signature(client, settings):
    """The per-app URL is identity, not authentication. An unsigned delivery to
    a perfectly good identifier is still dropped, with the signature reason."""
    from wa.models import WAWebhookEvent

    settings.META_APP_SECRET = SECRET
    app = _wa_app()

    response = client.post(
        _meta_url(app),
        data=_raw(_meta_body()),
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json()["reason"] == SIG_BAD_HEADER
    assert not WAWebhookEvent.objects.exists()


@pytest.mark.django_db
def test_the_verify_token_reports_the_scope_it_actually_has(settings):
    """#307 is not done, and the endpoint says so rather than implying a
    per-app token is being checked when the handshake checks a global one."""
    settings.META_WEBHOOK_VERIFY_TOKEN = "deployment-wide-token"
    app = _wa_app()

    token, scope = webhook_identity.verify_token(app)

    assert token == "deployment-wide-token"
    assert scope == "deployment"


# ─────────────────────────────────────────────────────────────────────────────
# The verification handshake on a per-app URL
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_the_handshake_on_an_apps_own_url_echoes_the_challenge(client, settings):
    """Meta's dashboard verifies the URL it is given, which is the per-app one,
    so the handshake has to work there or the URL cannot be registered."""
    settings.META_WEBHOOK_VERIFY_TOKEN = "tok-310"
    app = _wa_app()

    response = client.get(
        _meta_url(app),
        {"hub.mode": "subscribe", "hub.verify_token": "tok-310", "hub.challenge": "4242"},
    )

    assert response.status_code == 200
    assert response.content == b"4242"


@pytest.mark.django_db
def test_the_handshake_on_an_unowned_url_is_refused(client, settings):
    """403, not the 200 a delivery gets: a failed handshake is read by the
    person clicking "Verify and save", and echoing a challenge for a URL nobody
    owns would tell a scanner the shape is live."""
    settings.META_WEBHOOK_VERIFY_TOKEN = "tok-310"

    response = client.get(
        "/wa/v2/webhooks/meta/whk_" + "h" * 32 + "/",
        {"hub.mode": "subscribe", "hub.verify_token": "tok-310", "hub.challenge": "4242"},
    )

    assert response.status_code == 403
    assert response.json()["reason"] == APP_UNKNOWN_IDENTIFIER


@pytest.mark.django_db
def test_the_handshake_on_an_apps_own_url_still_checks_the_token(client, settings):
    settings.META_WEBHOOK_VERIFY_TOKEN = "tok-310"
    app = _wa_app()

    response = client.get(
        _meta_url(app),
        {"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "4242"},
    )

    assert response.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# Backwards compatibility: the legacy path is untouched
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_the_legacy_path_still_routes_from_the_body(client, settings):
    """The pinning test for the hard requirement. Self-hosters and the live
    deployment have this URL registered in Meta's App Dashboard; an upgrade must
    not require anyone to re-register it."""
    from wa.models import WAWebhookEvent

    settings.META_APP_SECRET = SECRET
    target = _wa_app()
    bystander = _wa_app()

    response = _post_signed(
        client,
        LEGACY_META_URL,
        _meta_body(waba_id=target.waba_id, phone_number_id=target.phone_number_id),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "received"
    assert WAWebhookEvent.objects.filter(wa_app=target).count() == 1
    assert not WAWebhookEvent.objects.filter(wa_app=bystander).exists()


@pytest.mark.django_db
def test_the_legacy_path_still_verifies_against_the_global_secret(client, settings):
    """Unchanged behaviour includes the rejections, not only the successes."""
    from wa.models import WAWebhookEvent

    settings.META_APP_SECRET = SECRET
    app = _wa_app()

    unsigned = client.post(LEGACY_META_URL, data=_raw(_meta_body()), content_type="application/json")
    assert unsigned.json()["reason"] == SIG_BAD_HEADER

    wrong_key = client.post(
        LEGACY_META_URL,
        data=_raw(_meta_body(waba_id=app.waba_id, phone_number_id=app.phone_number_id)),
        content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256=_sign(_raw(_meta_body()), "some-other-secret"),
    )
    assert wrong_key.json()["status"] == "ignored"
    assert not WAWebhookEvent.objects.exists()


@pytest.mark.django_db
def test_the_legacy_path_still_answers_an_unroutable_body_the_same_way(client, settings):
    """``unknown_app`` is what the legacy path has always said for a body no app
    matches, and tests on other branches post signed bodies here."""
    settings.META_APP_SECRET = SECRET
    _wa_app()

    response = _post_signed(client, LEGACY_META_URL, _meta_body())

    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "reason": "unknown_app"}


@pytest.mark.django_db
def test_the_legacy_path_never_consults_the_identifier(client, settings):
    """A delivery with no identifier in the path must not start doing an extra
    lookup — the legacy path is hot, and it has nothing to look up."""
    settings.META_APP_SECRET = SECRET
    app = _wa_app()

    with CaptureQueriesContext(connection) as captured:
        _post_signed(
            client,
            LEGACY_META_URL,
            _meta_body(waba_id=app.waba_id, phone_number_id=app.phone_number_id),
        )

    assert _identity_lookups(captured) == []


# ─────────────────────────────────────────────────────────────────────────────
# Any BSP, not only META (#305 D-4)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_a_gupshup_app_has_its_own_url_too(client):
    """Bring-your-own-app and Embedded Signup coexist permanently, so a
    Meta-only identity layer would have to be undone when #190 clears."""
    from wa.models import WAWebhookEvent

    app = _wa_app(bsp="GUPSHUP")

    response = client.post(
        _gupshup_url(app),
        data=_raw({"gs_app_id": app.app_id, "type": "message"}),
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json()["status"] == "received"
    assert WAWebhookEvent.objects.filter(wa_app=app).count() == 1


@pytest.mark.django_db
def test_a_gupshup_per_app_url_needs_no_app_id_in_the_body(client):
    """The URL is the identity, so a payload that omits ``gs_app_id`` — which
    the legacy path rejects outright — is now routable."""
    from wa.models import WAWebhookEvent

    app = _wa_app(bsp="GUPSHUP")

    response = client.post(
        _gupshup_url(app),
        data=_raw({"type": "message-event"}),
        content_type="application/json",
    )

    assert response.json()["status"] == "received"
    assert WAWebhookEvent.objects.filter(wa_app=app).count() == 1


@pytest.mark.django_db
def test_an_unknown_gupshup_identifier_is_also_200_and_counted(client):
    """The two receivers answer an unowned URL the same way, so nobody has to
    remember which BSP behaves differently."""
    from tenants.models import BSPChoices
    from wa.models import WAWebhookEvent

    before = webhook_identity.unknown_identifier_rejections(BSPChoices.GUPSHUP)

    response = client.post(
        "/wa/v2/webhooks/gupshup/whk_" + "g" * 32 + "/",
        data=_raw({"gs_app_id": "whatever"}),
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json()["reason"] == APP_UNKNOWN_IDENTIFIER
    assert not WAWebhookEvent.objects.exists()
    assert webhook_identity.unknown_identifier_rejections(BSPChoices.GUPSHUP) - before == 1


@pytest.mark.django_db
def test_the_gupshup_legacy_path_is_unchanged(client):
    """The Gupshup receiver shares this module, and #308's handshake work lives
    in it — breaking its legacy path would break work unrelated to #310."""
    from wa.models import WAWebhookEvent

    app = _wa_app(bsp="GUPSHUP")

    ok = client.post(
        LEGACY_GUPSHUP_URL,
        data=_raw({"gs_app_id": app.app_id, "type": "message"}),
        content_type="application/json",
    )
    assert ok.json()["status"] == "received"
    assert WAWebhookEvent.objects.filter(wa_app=app).count() == 1

    missing = client.post(LEGACY_GUPSHUP_URL, data=_raw({"type": "message"}), content_type="application/json")
    assert missing.status_code == 400

    unknown = client.post(
        LEGACY_GUPSHUP_URL,
        data=_raw({"gs_app_id": "no-such-app", "type": "message"}),
        content_type="application/json",
    )
    assert unknown.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# The endpoint a client-facing setup screen consumes
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_the_webhook_setup_endpoint_returns_the_pair_a_client_configures(settings):
    settings.DEFAULT_WEBHOOK_BASE_URL = "https://hooks.example.test"
    settings.META_WEBHOOK_VERIFY_TOKEN = "tok-310"

    tenant = _tenant()
    app = _wa_app(tenant)
    api = _api_client_for(tenant)

    response = api.get(f"/wa/v2/apps/{app.pk}/webhook-setup/")

    assert response.status_code == 200, response.data
    assert response.data == {
        "wa_app": str(app.pk),
        "bsp": "META",
        "callback_url": f"https://hooks.example.test/wa/v2/webhooks/meta/{app.webhook_identifier}/",
        "identifier_hint": webhook_identity.mask(app.webhook_identifier),
        "verify_token": "tok-310",
        "verify_token_scope": "deployment",
        "verify_token_configured": True,
    }


@pytest.mark.django_db
def test_the_url_the_endpoint_hands_out_is_the_url_that_works(client, settings):
    """The contract that matters: what a client pastes into their dashboard
    resolves back to their app. A typo in either half is invisible otherwise."""
    from wa.models import WAWebhookEvent

    settings.DEFAULT_WEBHOOK_BASE_URL = "https://hooks.example.test"
    settings.META_APP_SECRET = SECRET

    tenant = _tenant()
    app = _wa_app(tenant)
    api = _api_client_for(tenant)

    callback_url = api.get(f"/wa/v2/apps/{app.pk}/webhook-setup/").data["callback_url"]
    path = callback_url.replace("https://hooks.example.test", "")

    assert _post_signed(client, path, _meta_body()).json()["status"] == "received"
    assert WAWebhookEvent.objects.filter(wa_app=app).count() == 1


@pytest.mark.django_db
def test_the_setup_endpoint_is_not_open_to_every_role_that_can_read_the_app(settings):
    """The URL is a setup credential: whoever holds it can POST at the app's
    receiver. MANAGER can read the app list and must not get it (the line #251
    drew for BSP identifiers)."""
    tenant = _tenant()
    app = _wa_app(tenant)

    manager = _api_client_for(tenant, "manager")
    assert manager.get(f"/wa/v2/apps/{app.pk}/webhook-setup/").status_code == 403

    viewer = _api_client_for(tenant, "viewer")
    assert viewer.get(f"/wa/v2/apps/{app.pk}/webhook-setup/").status_code == 403


@pytest.mark.django_db
def test_the_identifier_is_not_exposed_through_the_ordinary_app_payloads():
    """It leaves through the gated setup endpoint or not at all — a field on the
    general serializer would hand it to every role that can read an app."""
    tenant = _tenant()
    app = _wa_app(tenant)
    api = _api_client_for(tenant)

    listing = api.get("/wa/v2/apps/")
    detail = api.get(f"/wa/v2/apps/{app.pk}/")

    assert app.webhook_identifier not in json.dumps(listing.data)
    assert app.webhook_identifier not in json.dumps(detail.data)


@pytest.mark.django_db
def test_an_unconfigured_verify_token_is_reported_as_unconfigured(settings):
    """A setup screen has to be able to say "configure the token first" rather
    than showing an empty field that looks like a value."""
    settings.META_WEBHOOK_VERIFY_TOKEN = ""

    tenant = _tenant()
    app = _wa_app(tenant)
    api = _api_client_for(tenant)

    body = api.get(f"/wa/v2/apps/{app.pk}/webhook-setup/").data

    assert body["verify_token"] == ""
    assert body["verify_token_configured"] is False


@pytest.mark.django_db
def test_the_callback_url_prefers_the_configured_public_base_url(settings):
    """The ``Host`` header is client-supplied and behind a proxy is frequently
    an internal name; the URL is about to be registered with Meta."""
    settings.DEFAULT_WEBHOOK_BASE_URL = "https://configured.example.test"

    tenant = _tenant()
    app = _wa_app(tenant)
    api = _api_client_for(tenant)

    response = api.get(f"/wa/v2/apps/{app.pk}/webhook-setup/", HTTP_HOST="attacker.example.com")

    assert response.data["callback_url"].startswith("https://configured.example.test/")
