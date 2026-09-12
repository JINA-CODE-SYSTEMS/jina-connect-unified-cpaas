"""Gupshup webhook verification handshake (#308).

``GupshupWebhookView.get`` read ``hub.verify_token`` as a bare expression and
threw the value away::

    request.GET.get("hub.verify_token")

Nothing was assigned and nothing was compared, so the handshake completed for
*any* presented token — a wrong one, or none at all. Anyone who could reach the
endpoint could claim ownership of it and point a webhook subscription at us.

The success path looked perfectly healthy the whole time, which is why this
shipped: a test that only asks "does the right token get the challenge back?"
passes against the broken code too. So every test here that matters asserts on
a *rejection*, and asserts the challenge is not echoed in it.

HOW TO RUN:
    python -m pytest wa/tests/test_gupshup_webhook_verification.py -v
"""

from __future__ import annotations

import logging

import pytest
from django.test import override_settings

# The handshake itself touches no model, but ``DEBUG=True`` (how CI runs) puts
# django-silk's middleware in front of every request, and it writes a row per
# request — so the client needs a database regardless.
pytestmark = pytest.mark.django_db

GUPSHUP_WEBHOOK_URL = "/wa/v2/webhooks/gupshup/"
META_WEBHOOK_URL = "/wa/v2/webhooks/meta/"

TOKEN = "gs-verify-token-correct"
CHALLENGE = "1158201444"


def _handshake(client, **params):
    """GET the Gupshup webhook with a subscribe handshake."""
    query = {"hub.mode": "subscribe", "hub.challenge": CHALLENGE}
    query.update(params)
    return client.get(GUPSHUP_WEBHOOK_URL, query)


# ─────────────────────────────────────────────────────────────────────────────
# Rejections — the half the old code got wrong
# ─────────────────────────────────────────────────────────────────────────────


@override_settings(GUPSHUP_WEBHOOK_VERIFY_TOKEN=TOKEN)
def test_wrong_token_is_rejected_and_challenge_not_echoed(client):
    """A wrong token gets 403, and the challenge never leaves the building."""
    resp = _handshake(client, **{"hub.verify_token": "not-the-token"})

    assert resp.status_code == 403
    assert CHALLENGE not in resp.content.decode()
    assert resp.json() == {"error": "Verify token mismatch"}


@override_settings(GUPSHUP_WEBHOOK_VERIFY_TOKEN=TOKEN)
def test_absent_token_is_rejected(client):
    """No ``hub.verify_token`` at all is still a failed handshake, not a pass."""
    resp = _handshake(client)

    assert resp.status_code == 403
    assert CHALLENGE not in resp.content.decode()


@override_settings(GUPSHUP_WEBHOOK_VERIFY_TOKEN=TOKEN)
def test_empty_token_is_rejected(client):
    """``hub.verify_token=`` is a presented-and-wrong token, not a missing one."""
    resp = _handshake(client, **{"hub.verify_token": ""})

    assert resp.status_code == 403
    assert CHALLENGE not in resp.content.decode()


# ─────────────────────────────────────────────────────────────────────────────
# Success — echo the challenge as plain text
# ─────────────────────────────────────────────────────────────────────────────


@override_settings(GUPSHUP_WEBHOOK_VERIFY_TOKEN=TOKEN)
def test_correct_token_echoes_challenge_as_plain_text(client):
    resp = _handshake(client, **{"hub.verify_token": TOKEN})

    assert resp.status_code == 200
    assert resp.content.decode() == CHALLENGE
    assert resp["Content-Type"].startswith("text/plain")


@override_settings(GUPSHUP_WEBHOOK_VERIFY_TOKEN=TOKEN)
def test_non_subscribe_mode_is_rejected(client):
    """Only ``hub.mode=subscribe`` is a verification request."""
    resp = client.get(
        GUPSHUP_WEBHOOK_URL,
        {"hub.mode": "unsubscribe", "hub.challenge": CHALLENGE, "hub.verify_token": TOKEN},
    )

    assert resp.status_code == 403
    assert CHALLENGE not in resp.content.decode()


# ─────────────────────────────────────────────────────────────────────────────
# Unset secret — documented, and identical to MetaWebhookView.get
# ─────────────────────────────────────────────────────────────────────────────


@override_settings(GUPSHUP_WEBHOOK_VERIFY_TOKEN="", META_WEBHOOK_VERIFY_TOKEN="")
def test_unset_secret_skips_the_check_like_meta_does(client):
    """With no secret configured there is nothing to compare against.

    The view then echoes the challenge, which is exactly what
    ``MetaWebhookView.get`` does with an empty ``META_WEBHOOK_VERIFY_TOKEN``.
    Pinned here so the two handshakes stay the same rule rather than drifting
    into two different ones.
    """
    resp = _handshake(client, **{"hub.verify_token": "anything-at-all"})
    assert resp.status_code == 200
    assert resp.content.decode() == CHALLENGE

    meta = client.get(
        META_WEBHOOK_URL,
        {"hub.mode": "subscribe", "hub.challenge": CHALLENGE, "hub.verify_token": "anything-at-all"},
    )
    assert resp.status_code == meta.status_code
    assert resp.content.decode() == meta.content.decode()


# ─────────────────────────────────────────────────────────────────────────────
# The presented token is a secret guess — it must not reach the logs
# ─────────────────────────────────────────────────────────────────────────────


class _Capture(logging.Handler):
    """Collects formatted records, the way a real handler would write them.

    ``caplog`` alone is not enough here: the project's LOGGING config gives the
    ``wa`` logger ``propagate=False``, so its records never reach the root
    handler pytest attaches. This listens where the view actually logs.
    """

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(self.format(record))

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def _capture_wa_logs():
    handler = _Capture()
    handler.setFormatter(logging.Formatter("[{levelname}] {name}: {message}", style="{"))
    logger = logging.getLogger("wa")
    logger.addHandler(handler)
    return handler, logger


@override_settings(GUPSHUP_WEBHOOK_VERIFY_TOKEN=TOKEN)
def test_presented_token_never_appears_in_log_output(client, caplog):
    """Mismatches are logged, but never with the value that was presented.

    ``MetaWebhookView.get`` logs ``got=%s`` on mismatch; this endpoint must not
    copy that, so an attacker's guesses (and a correct token sent to the wrong
    endpoint) stay out of the log sinks.
    """
    presented = "s3cret-guess-should-not-be-logged"

    handler, logger = _capture_wa_logs()
    try:
        with caplog.at_level(logging.DEBUG):
            resp = _handshake(client, **{"hub.verify_token": presented})
    finally:
        logger.removeHandler(handler)

    assert resp.status_code == 403

    output = "\n".join([handler.text, caplog.text])
    assert presented not in output
    assert TOKEN not in output
    # Silence is not the fix: the rejection is still reported, just without the value.
    assert "mismatch" in handler.text.lower()


@override_settings(GUPSHUP_WEBHOOK_VERIFY_TOKEN=TOKEN)
def test_correct_token_is_not_logged_either(client, caplog):
    handler, logger = _capture_wa_logs()
    try:
        with caplog.at_level(logging.DEBUG):
            resp = _handshake(client, **{"hub.verify_token": TOKEN})
    finally:
        logger.removeHandler(handler)

    assert resp.status_code == 200
    assert TOKEN not in "\n".join([handler.text, caplog.text])
