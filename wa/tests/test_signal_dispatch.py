"""Signal dispatch and the unprocessed-webhook backlog (#269).

`wa/signals.py` did `from jina_connect import settings` — the settings
*module*, not `django.conf.settings`. Two consequences:

* the gate could not be overridden by tests or ``@override_settings``, so none
  of this was reachable from a test at all;
* it read a module literal whose default is ``redis://localhost:6379/0``, so
  the check asked "is a string set", never "is a queue reachable". On a
  deployment with no consumer it dispatched happily into nothing.

HOW TO RUN:
    .venv/bin/python -m pytest wa/tests/test_signal_dispatch.py -v
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from django.test import override_settings

from wa.signals import _dispatch


def _task():
    task = MagicMock()
    task.delay = MagicMock()
    return task


# ─────────────────────────────────────────────────────────────────────────────
# The import bug itself
# ─────────────────────────────────────────────────────────────────────────────


def test_the_module_reads_settings_through_django():
    """Regression guard for the actual defect.

    With the settings module imported directly, everything below silently
    tested the wrong object.
    """
    from django.conf import settings as django_settings

    import wa.signals as signals

    assert signals.settings is django_settings


@override_settings(CELERY_BROKER_URL="redis://localhost:6379/0")
def test_a_configured_broker_is_used():
    task = _task()
    _dispatch(task, what="test", pk="abc")

    task.delay.assert_called_once_with("abc")
    task.assert_not_called()


@override_settings(CELERY_BROKER_URL="")
def test_no_broker_runs_in_process_instead_of_dropping_the_row():
    """The missing ``else``: the event was stored and abandoned."""
    task = _task()
    _dispatch(task, what="test", pk="abc")

    task.delay.assert_not_called()
    task.assert_called_once_with("abc")


# ─────────────────────────────────────────────────────────────────────────────
# The case the old check could never catch
# ─────────────────────────────────────────────────────────────────────────────


@override_settings(CELERY_BROKER_URL="redis://localhost:6379/0")
def test_an_unreachable_broker_falls_back_rather_than_dispatching_into_nothing():
    """A non-empty URL is not evidence of a consumer.

    This is the deployment shape that actually loses messages: the variable is
    set (it has a non-empty default), the guard passes, and the task goes to a
    queue nobody reads.
    """
    task = _task()
    task.delay.side_effect = OSError("Connection refused")

    _dispatch(task, what="test", pk="abc")

    task.delay.assert_called_once_with("abc")
    task.assert_called_once_with("abc")


@override_settings(CELERY_BROKER_URL="redis://localhost:6379/0")
def test_a_failure_in_the_fallback_is_not_swallowed():
    """If in-process work fails too, the caller must hear about it."""
    task = _task()
    task.delay.side_effect = OSError("Connection refused")
    task.side_effect = ValueError("parser blew up")

    with pytest.raises(ValueError, match="parser blew up"):
        _dispatch(task, what="test", pk="abc")


# ─────────────────────────────────────────────────────────────────────────────
# The backlog, which is the symptom shared by every one of those modes
# ─────────────────────────────────────────────────────────────────────────────


def _event(**overrides):
    import uuid

    from tenants.models import Tenant
    from wa.models import WAApp, WAWebhookEvent

    tenant = Tenant.objects.create(name=f"BacklogTenant-{uuid.uuid4().hex[:6]}", is_active=True)
    wa_app = WAApp.objects.create(
        tenant=tenant,
        app_name=f"app-{uuid.uuid4().hex[:6]}",
        app_id=f"a-{uuid.uuid4().hex[:6]}",
        app_secret="s",
        wa_number=f"+1{uuid.uuid4().int % 10**10:010d}",
        bsp="META",
    )
    # Always create as processed: the post_save signal now genuinely runs
    # processing in-process when there is no broker (which is the fix under
    # test, and which conftest arranges), so a row created unprocessed would
    # be processed before the assertion. Tests that want a stuck row set the
    # flag back afterwards with .update(), which does not re-fire the signal.
    fields = {"wa_app": wa_app, "bsp": "META", "event_type": "MESSAGE", "payload": {}, "is_processed": True}
    fields.update(overrides)
    return WAWebhookEvent.objects.create(**fields)


def _stuck_event(minutes_old: int = 120):
    """An event that was stored and never processed."""
    from django.utils import timezone

    from wa.models import WAWebhookEvent

    event = _event()
    WAWebhookEvent.objects.filter(pk=event.pk).update(
        is_processed=False,
        created_at=timezone.now() - timezone.timedelta(minutes=minutes_old),
    )
    return event


@pytest.mark.django_db
def test_the_backlog_command_is_quiet_when_there_is_none():
    from django.core.management import call_command

    _event()
    call_command("webhook_backlog")  # no SystemExit


@pytest.mark.django_db
def test_the_backlog_command_fails_when_events_are_stuck():
    from django.core.management import call_command

    _stuck_event()

    with pytest.raises(SystemExit):
        call_command("webhook_backlog")


@pytest.mark.django_db
def test_recent_events_are_in_flight_not_stuck():
    """Something queued seconds ago has not failed yet."""
    from django.core.management import call_command

    _stuck_event(minutes_old=1)
    call_command("webhook_backlog", older_than=10)  # no SystemExit


@pytest.mark.django_db
def test_the_threshold_is_configurable():
    from django.core.management import call_command

    _stuck_event()
    call_command("webhook_backlog", max=5)  # tolerated

    with pytest.raises(SystemExit):
        call_command("webhook_backlog", max=0)
