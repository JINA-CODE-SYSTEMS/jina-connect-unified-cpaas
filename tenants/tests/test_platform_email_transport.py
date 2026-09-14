"""Platform email is provider-agnostic, and the ways of getting that wrong.

Nothing in the email path is tied to one provider — it is Django's plain SMTP
backend, so Gmail with an app password, xneelo, Hetzner or a corporate relay
differ only in environment variables. What was missing was the *other*
encrypted transport: port 465's implicit SSL had no setting at all, so a
deployment whose provider offers 465 could not be configured from a .env.

The tests below are in two halves, and the distinction matters.

``EMAIL_USE_SSL`` and ``EMAIL_TIMEOUT`` are Django settings that have always
existed in ``global_settings``; the backend has always read them. What did not
exist was any way to *set* them from the environment — ``jina_connect.settings``
never read them, so putting either in a .env did nothing at all. So the tests
that assert the backend honours a transport are pinning **Django**, not this
change, and say so; the ones that prove this change run the settings module in
a subprocess with an environment, which is the only place the wiring is real.

HOW TO RUN:
    python -m pytest tenants/tests/test_platform_email_transport.py -v
"""

from __future__ import annotations

import os
import smtplib
import subprocess
import sys
from email.utils import parseaddr
from io import StringIO
from pathlib import Path

import pytest
from django.core.mail import get_connection
from django.core.management import call_command
from django.test import override_settings

SMTP_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
LOCMEM_BACKEND = "django.core.mail.backends.locmem.EmailBackend"


# ─────────────────────────────────────────────────────────────────────────────
# Premise: the backend honours each transport. Django's behaviour, not ours —
# here so that the settings wiring below is known to be wiring to something.
# ─────────────────────────────────────────────────────────────────────────────


@override_settings(EMAIL_BACKEND=SMTP_BACKEND, EMAIL_PORT=465, EMAIL_USE_SSL=True, EMAIL_USE_TLS=False)
def test_implicit_ssl_reaches_the_backend():
    """Port 465, the configuration that could not be expressed in a .env before.

    This would pass before this change too — ``override_settings`` can set a
    setting the project never reads. It is the premise, not the proof.
    """
    connection = get_connection()

    assert connection.use_ssl is True
    assert connection.use_tls is False
    assert connection.port == 465


@override_settings(EMAIL_BACKEND=SMTP_BACKEND, EMAIL_PORT=587, EMAIL_USE_TLS=True, EMAIL_USE_SSL=False)
def test_starttls_still_reaches_the_backend():
    """The existing answer, unmoved. Every deployment today is on this path."""
    connection = get_connection()

    assert connection.use_tls is True
    assert connection.use_ssl is False
    assert connection.port == 587


@override_settings(EMAIL_BACKEND=SMTP_BACKEND, EMAIL_USE_TLS=True, EMAIL_USE_SSL=True)
def test_both_transports_at_once_is_refused_by_django():
    """Where Django raises, and why the startup guard below exists.

    Not at boot — here, inside ``EmailBackend.__init__``, which runs on the
    first *send*. A deployment that set both would come up clean and fail on
    somebody's password reset.
    """
    with pytest.raises(ValueError, match="mutually exclusive"):
        get_connection()


# ─────────────────────────────────────────────────────────────────────────────
# Proof: the environment reaches the settings. This is the change.
#
# In a subprocess because that is the only honest way to test it — the settings
# module is read once per process, and ``override_settings`` bypasses it
# entirely, so an in-process test would pass on a project that reads no
# environment variable at all. Which is exactly the state before this change.
# ─────────────────────────────────────────────────────────────────────────────


def _settings_under(env: dict) -> subprocess.CompletedProcess:
    """Boot Django with *env* applied and report the email settings it ends up with."""
    script = (
        "import django; django.setup()\n"
        "from django.conf import settings as s\n"
        "print(f'{s.EMAIL_USE_TLS}|{s.EMAIL_USE_SSL}|{s.EMAIL_TIMEOUT}|{s.EMAIL_PORT}')\n"
    )
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env={**os.environ, **env},
        cwd=Path(__file__).resolve().parents[2],
        timeout=120,
    )


def test_asking_for_ssl_alone_is_a_complete_instruction():
    """``EMAIL_USE_SSL=True`` in a .env, and nothing else.

    Before this change the variable was not read, so this produced STARTTLS on
    port 465 — which does not error, it hangs. It now also turns STARTTLS off,
    because leaving it on is the one combination Django refuses outright.
    """
    result = _settings_under({"EMAIL_USE_SSL": "True", "EMAIL_PORT": "465"})

    assert result.returncode == 0, result.stderr
    use_tls, use_ssl, _timeout, port = result.stdout.strip().split("|")
    assert (use_ssl, use_tls, port) == ("True", "False", "465")


def test_an_explicit_starttls_still_wins():
    """The override is not one-way: a deployment that means both settings is
    told so, rather than having one of them silently rewritten."""
    result = _settings_under({"EMAIL_USE_SSL": "True", "EMAIL_USE_TLS": "True"})

    assert result.returncode != 0
    assert "mutually exclusive" in result.stderr
    assert "465" in result.stderr, "the refusal has to say which port wants which setting"


def test_the_default_deployment_is_unchanged():
    """Every deployment today sets neither, and must keep STARTTLS on 587."""
    result = _settings_under({})

    assert result.returncode == 0, result.stderr
    use_tls, use_ssl, _timeout, port = result.stdout.strip().split("|")
    assert (use_tls, use_ssl, port) == ("True", "False", "587")


def test_the_timeout_is_set_by_default_and_settable():
    """Django's own default is ``None`` — block forever.

    Outbound SMTP is filtered by default on most cloud egress, so "no answer"
    is an ordinary outcome rather than an exotic one, and without a timeout it
    wedges the thread that is sending instead of raising.
    """
    assert _settings_under({}).stdout.strip().split("|")[2] == "30"
    assert _settings_under({"EMAIL_TIMEOUT": "5"}).stdout.strip().split("|")[2] == "5"


# ─────────────────────────────────────────────────────────────────────────────
# The check command: it has to *report* a failure, not raise one
# ─────────────────────────────────────────────────────────────────────────────


def _run_check(**kwargs) -> str:
    out = StringIO()
    call_command("check_email", stdout=out, **kwargs)
    return out.getvalue()


@override_settings(
    EMAIL_BACKEND=SMTP_BACKEND,
    EMAIL_HOST="smtp.example.test",
    EMAIL_PORT=465,
    EMAIL_USE_SSL=True,
    EMAIL_USE_TLS=False,
    EMAIL_HOST_USER="noreply@example.test",
    EMAIL_HOST_PASSWORD="secret",  # noqa: S106 — test fixture
    DEFAULT_FROM_EMAIL="noreply@example.test",
)
def test_the_check_reports_the_transport_it_would_use(monkeypatch):
    monkeypatch.setattr("django.core.mail.backends.smtp.EmailBackend.open", lambda self: True)
    monkeypatch.setattr("django.core.mail.backends.smtp.EmailBackend.close", lambda self: None)

    output = _run_check()

    assert "smtp.example.test:465" in output
    assert "implicit SSL" in output
    assert "Connected and authenticated" in output


@override_settings(
    EMAIL_BACKEND=SMTP_BACKEND,
    EMAIL_HOST_USER="noreply@example.test",
    EMAIL_HOST_PASSWORD="wrong",  # noqa: S106 — test fixture
)
def test_a_rejected_login_is_reported_rather_than_raised(monkeypatch):
    """A diagnostic that dies with a traceback is worse than the 500 it replaces."""

    def refuse(self):
        raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Authentication credentials invalid")

    monkeypatch.setattr("django.core.mail.backends.smtp.EmailBackend.open", refuse)

    output = _run_check()

    assert "Rejected the credentials" in output
    assert "EMAIL_HOST_USER or EMAIL_HOST_PASSWORD" in output


@override_settings(
    EMAIL_BACKEND=SMTP_BACKEND,
    EMAIL_TIMEOUT=5,
    EMAIL_PORT=465,
    EMAIL_USE_SSL=False,
    EMAIL_USE_TLS=True,
)
def test_a_timeout_names_the_465_mistake(monkeypatch):
    """The specific misconfiguration this change makes possible to fix.

    STARTTLS against an SSL-only port hangs rather than failing, so the only
    thing the operator sees is a timeout — which looks like a firewall. The
    message has to name the other possibility or it sends them to the wrong
    place, which is the #365 lesson.
    """

    def hang(self):
        raise TimeoutError

    monkeypatch.setattr("django.core.mail.backends.smtp.EmailBackend.open", hang)

    output = _run_check()

    assert "EMAIL_USE_SSL=True" in output


@override_settings(
    EMAIL_BACKEND=SMTP_BACKEND,
    EMAIL_HOST_USER="mailbox@xneelo-example.test",
    EMAIL_HOST_PASSWORD="secret",  # noqa: S106 — test fixture
    DEFAULT_FROM_EMAIL="Jina Connect <noreply@somewhere-else.test>",
)
def test_a_sender_that_is_not_the_mailbox_is_flagged_before_sending(monkeypatch):
    """The failure that looks like a code bug.

    A provider accepts the login and then refuses the message at ``MAIL FROM``,
    so "SMTP works, mail does not" sends people to read the sending code. It is
    a mismatch between two environment variables, and it is visible without
    sending anything.
    """
    monkeypatch.setattr("django.core.mail.backends.smtp.EmailBackend.open", lambda self: True)
    monkeypatch.setattr("django.core.mail.backends.smtp.EmailBackend.close", lambda self: None)

    output = _run_check()

    assert "noreply@somewhere-else.test" in output
    assert "mailbox@xneelo-example.test" in output


@override_settings(
    EMAIL_BACKEND=SMTP_BACKEND,
    EMAIL_HOST_USER="mailbox@example.test",
    DEFAULT_FROM_EMAIL="Jina Connect <mailbox@example.test>",
)
def test_a_display_name_is_not_treated_as_a_mismatch(monkeypatch):
    """``Name <addr>`` is the same sender as ``addr``.

    Parsed rather than compared as strings, because every real configuration
    carries a display name and a warning that fires on all of them is one
    nobody reads.
    """
    monkeypatch.setattr("django.core.mail.backends.smtp.EmailBackend.open", lambda self: True)
    monkeypatch.setattr("django.core.mail.backends.smtp.EmailBackend.close", lambda self: None)

    assert parseaddr("Jina Connect <mailbox@example.test>")[1] == "mailbox@example.test"
    assert "while authenticating as" not in _run_check()


@override_settings(EMAIL_BACKEND=LOCMEM_BACKEND)
def test_a_non_smtp_backend_is_called_out_rather_than_passing():
    """A console backend answers yes to every question this command asks."""
    output = _run_check()

    assert "not SMTP" in output
    assert "Connected and authenticated" not in output


@override_settings(
    EMAIL_BACKEND=SMTP_BACKEND,
    EMAIL_HOST_USER="mailbox@example.test",
    DEFAULT_FROM_EMAIL="mailbox@example.test",
)
def test_a_refused_sender_is_reported_with_what_to_change(monkeypatch):
    monkeypatch.setattr("django.core.mail.backends.smtp.EmailBackend.open", lambda self: True)
    monkeypatch.setattr("django.core.mail.backends.smtp.EmailBackend.close", lambda self: None)

    def refuse(self, fail_silently=False):
        raise smtplib.SMTPSenderRefused(550, b"5.7.1 Sender address rejected", "mailbox@example.test")

    monkeypatch.setattr("django.core.mail.message.EmailMessage.send", refuse)

    output = _run_check(to="someone@example.test")

    assert "The sender was refused" in output
    assert "DEFAULT_FROM_EMAIL" in output
