"""META webhook signature verification (#306, fail-open half).

``_verify_meta_signature`` returned ``True`` when ``META_APP_SECRET`` was
unset, so a public, unauthenticated endpoint accepted any well-formed body:
anyone who learned or guessed a ``waba_id`` could inject inbound messages,
delivery statuses and template decisions into any tenant, and a single log
line was the only trace.

The property that matters here is not "a good signature works" — that already
worked. It is that **a delivery which cannot be verified is never ingested**,
and that the rejection says *which* of the three failure modes fired, because
META is always answered with 200 (a non-200 throttles delivery) and the reason
code is therefore the only thing distinguishing them.

Everything here posts to the **legacy unsuffixed** receiver, which has no app
identity at the point the signature is checked and so verifies against the
deployment-wide secret. That path is unchanged by #306's second half, and these
tests are what pins it unchanged. Per-app secret selection is covered in
``wa/tests/test_per_app_signature_verification.py``.

HOW TO RUN:
    .venv/bin/python -m pytest wa/tests/test_meta_webhook_signature.py -v
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from wa.views import SIG_BAD_HEADER, SIG_MISMATCH, SIG_UNVERIFIABLE

URL = "/wa/v2/webhooks/meta/"

# Distinctive so a leak into a log line cannot be mistaken for anything else.
SECRET = "s3cr3t-app-secret-must-never-be-logged"
OTHER_SECRET = "another-clients-app-secret-8f2a"
WABA = "waba-sig-1"
PHONE_NUMBER_ID = "pn-sig-1"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _body() -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": WABA,
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "metadata": {"phone_number_id": PHONE_NUMBER_ID},
                            "messages": [
                                {
                                    "id": "wamid.sig-1",
                                    "from": "919000000001",
                                    "timestamp": "1700000000",
                                    "type": "text",
                                    "text": {"body": "hello"},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


def _raw(body: dict | None = None) -> bytes:
    return json.dumps(body if body is not None else _body()).encode("utf-8")


def _sign(raw: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()


def _post(client, raw: bytes, signature: str | None = None):
    extra = {} if signature is None else {"HTTP_X_HUB_SIGNATURE_256": signature}
    return client.post(URL, data=raw, content_type="application/json", **extra)


def _wa_app():
    from tenants.models import Tenant
    from wa.models import WAApp

    tenant = Tenant.objects.create(name=f"SigTenant-{uuid.uuid4().hex[:6]}", is_active=True)
    return WAApp.objects.create(
        tenant=tenant,
        app_name=f"app-{uuid.uuid4().hex[:6]}",
        app_id=f"a-{uuid.uuid4().hex[:6]}",
        app_secret="s",
        wa_number=f"+1{uuid.uuid4().int % 10**10:010d}",
        waba_id=WABA,
        phone_number_id=PHONE_NUMBER_ID,
        bsp="META",
        bsp_credentials={"access_token": "tok"},
        is_active=True,
    )


class _Collector(logging.Handler):
    """Collects rendered messages from ``wa.views``.

    ``caplog`` cannot be used: the ``wa`` logger is configured with
    ``propagate: False``, so its records never reach the root handler pytest
    installs.
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
    """Ingestion is what is under test, not the downstream Celery pipeline."""
    with patch("wa.signals._dispatch"):
        yield


# ─────────────────────────────────────────────────────────────────────────────
# The regression: an unset secret must reject, not accept
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_an_unset_secret_rejects_rather_than_accepting(client, settings):
    """The fail-open regression. This is the whole of #306's first fault."""
    from wa.models import WAWebhookEvent

    settings.META_APP_SECRET = ""
    settings.META_WEBHOOK_ALLOW_UNSIGNED = False
    settings.DEBUG = False
    _wa_app()

    response = _post(client, _raw())

    assert response.json()["reason"] == SIG_UNVERIFIABLE
    assert response.json()["status"] == "ignored"
    assert not WAWebhookEvent.objects.exists(), "an unverifiable delivery must not be ingested"


@pytest.mark.django_db
def test_an_unset_secret_rejects_even_a_body_an_attacker_signed(client, settings):
    """With no secret there is nothing to check against, so a signature that
    *looks* right proves nothing and must not buy acceptance."""
    from wa.models import WAWebhookEvent

    settings.META_APP_SECRET = ""
    settings.META_WEBHOOK_ALLOW_UNSIGNED = False
    settings.DEBUG = False
    _wa_app()

    raw = _raw()
    response = _post(client, raw, _sign(raw, OTHER_SECRET))

    assert response.json()["reason"] == SIG_UNVERIFIABLE
    assert not WAWebhookEvent.objects.exists()


# ─────────────────────────────────────────────────────────────────────────────
# The development escape hatch
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_the_dev_escape_hatch_refuses_to_engage_when_debug_is_false(client, settings):
    """``META_WEBHOOK_ALLOW_UNSIGNED`` must be inert in production.

    Without the ``DEBUG`` condition the escape hatch is just the original
    fail-open with a flag in front of it.
    """
    from wa.models import WAWebhookEvent

    settings.META_APP_SECRET = ""
    settings.META_WEBHOOK_ALLOW_UNSIGNED = True
    settings.DEBUG = False
    _wa_app()

    response = _post(client, _raw())

    assert response.status_code == 200
    assert response.json()["reason"] == SIG_UNVERIFIABLE
    assert not WAWebhookEvent.objects.exists()


@pytest.mark.django_db
def test_the_dev_escape_hatch_engages_only_together_with_debug(client, settings):
    """The other half of the pair: it does work locally, or it is not an
    escape hatch at all."""
    from wa.models import WAWebhookEvent

    settings.META_APP_SECRET = ""
    settings.META_WEBHOOK_ALLOW_UNSIGNED = True
    settings.DEBUG = True
    app = _wa_app()

    response = _post(client, _raw())

    assert response.json()["status"] == "received"
    assert WAWebhookEvent.objects.filter(wa_app=app).count() == 1


@pytest.mark.django_db
def test_debug_alone_does_not_disable_verification(client, settings):
    """``DEBUG`` is not itself the switch — the flag has to be set too."""
    settings.META_APP_SECRET = ""
    settings.META_WEBHOOK_ALLOW_UNSIGNED = False
    settings.DEBUG = True
    _wa_app()

    assert _post(client, _raw()).json()["reason"] == SIG_UNVERIFIABLE


# ─────────────────────────────────────────────────────────────────────────────
# Header and signature failures get their own reason codes
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_an_absent_signature_header_is_rejected_with_its_own_reason(client, settings):
    from wa.models import WAWebhookEvent

    settings.META_APP_SECRET = SECRET
    _wa_app()

    response = _post(client, _raw(), signature=None)

    assert response.json()["reason"] == SIG_BAD_HEADER
    assert not WAWebhookEvent.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "header",
    [
        "",
        "deadbeef",
        "sha1=deadbeef",
        "SHA256=deadbeef",
        "sha256",
    ],
)
def test_a_malformed_signature_header_is_rejected_with_its_own_reason(client, settings, header):
    from wa.models import WAWebhookEvent

    settings.META_APP_SECRET = SECRET
    _wa_app()

    response = _post(client, _raw(), header)

    assert response.json()["reason"] == SIG_BAD_HEADER
    assert not WAWebhookEvent.objects.exists()


@pytest.mark.django_db
def test_a_body_signed_with_another_secret_is_a_mismatch_not_a_header_fault(client, settings):
    """The reason codes have to separate "you sent nothing usable" from
    "you signed with the wrong key" — the second is the rotated-secret case
    that otherwise goes quiet."""
    from wa.models import WAWebhookEvent

    settings.META_APP_SECRET = SECRET
    _wa_app()

    raw = _raw()
    response = _post(client, raw, _sign(raw, OTHER_SECRET))

    assert response.json()["reason"] == SIG_MISMATCH
    assert not WAWebhookEvent.objects.exists()


@pytest.mark.django_db
def test_a_tampered_body_is_rejected(client, settings):
    from wa.models import WAWebhookEvent

    settings.META_APP_SECRET = SECRET
    _wa_app()

    signed = _raw()
    tampered = signed.replace(b"hello", b"HELLO")
    assert tampered != signed

    response = _post(client, tampered, _sign(signed, SECRET))

    assert response.json()["reason"] == SIG_MISMATCH
    assert not WAWebhookEvent.objects.exists()


@pytest.mark.django_db
def test_a_valid_signature_is_accepted(client, settings):
    from wa.models import WAWebhookEvent

    settings.META_APP_SECRET = SECRET
    app = _wa_app()

    raw = _raw()
    response = _post(client, raw, _sign(raw, SECRET))

    assert response.json()["status"] == "received"
    assert WAWebhookEvent.objects.filter(wa_app=app).count() == 1


@pytest.mark.django_db
def test_the_comparison_is_constant_time(client, settings):
    """Guards the one property a rewrite of this function could silently drop."""
    import wa.views as views

    settings.META_APP_SECRET = SECRET
    _wa_app()

    calls: list[tuple] = []
    real_compare = hmac.compare_digest

    def _recording_compare(a, b):
        calls.append((a, b))
        return real_compare(a, b)

    raw = _raw()
    views.hmac.compare_digest = _recording_compare
    try:
        assert _post(client, raw, _sign(raw, SECRET)).json()["status"] == "received"
    finally:
        views.hmac.compare_digest = real_compare

    assert calls, "signature comparison must go through hmac.compare_digest"


# ─────────────────────────────────────────────────────────────────────────────
# Contract with META: every outcome is a 200
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("secret", "allow_unsigned", "debug", "signature", "expected_reason"),
    [
        ("", False, False, None, SIG_UNVERIFIABLE),
        ("", True, False, None, SIG_UNVERIFIABLE),
        (SECRET, False, False, None, SIG_BAD_HEADER),
        (SECRET, False, False, "garbage", SIG_BAD_HEADER),
        (SECRET, False, False, "sha256=00", SIG_MISMATCH),
    ],
)
def test_every_rejection_still_answers_meta_with_200(
    client, settings, secret, allow_unsigned, debug, signature, expected_reason
):
    """A non-200 makes META throttle delivery, so a rejection must not become
    a 4xx just because it is now a rejection."""
    settings.META_APP_SECRET = secret
    settings.META_WEBHOOK_ALLOW_UNSIGNED = allow_unsigned
    settings.DEBUG = debug
    _wa_app()

    response = _post(client, _raw(), signature)

    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "reason": expected_reason}


# ─────────────────────────────────────────────────────────────────────────────
# Nothing secret reaches the logs
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
@pytest.mark.parametrize("configure_secret", [True, False])
def test_no_secret_or_signature_value_reaches_a_log_line(client, settings, configure_secret):
    """Rejections are logged, and the logs are where a leak would end up.

    Scoped to the POST path; the GET verification handshake is #307.
    """
    settings.META_APP_SECRET = SECRET if configure_secret else ""
    settings.META_WEBHOOK_ALLOW_UNSIGNED = False
    settings.DEBUG = False
    _wa_app()

    raw = _raw()
    good_signature = _sign(raw, SECRET)
    digest = good_signature.split("=", 1)[1]

    with _view_logs() as logs:
        _post(client, raw)  # no header
        _post(client, raw, "garbage")  # malformed header
        _post(client, raw, _sign(raw, OTHER_SECRET))  # mismatch
        _post(client, raw, good_signature)  # valid, when a secret is set

    assert logs.lines, "rejections must leave a trace"
    assert SECRET not in logs.text
    assert OTHER_SECRET not in logs.text
    assert digest not in logs.text
    # The setting's *name* is fine and useful; its value is not.
    assert "META_APP_SECRET=" not in logs.text
