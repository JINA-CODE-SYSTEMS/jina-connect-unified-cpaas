"""The inbound idempotency key, at the database level (#330).

The sibling integration tests — ``test_meta_path_inbound.py`` and
``chat_flow/test_meta_path_trigger_dispatch.py`` — prove that a redelivered
Meta webhook no longer doubles the inbox or re-fires a flow. They exercise the
*code path*, which is the cheap half of the guard: one indexed read that sees
the first delivery's row and returns.

That half is not the guarantee. Meta can redeliver concurrently and two
workers can both pass a read-then-write check, so the promise has to come from
the schema. What this module pins is the partial unique index itself:

* it rejects a second inbound row carrying the same provider message id,
* it does *not* constrain the rows that legitimately have no such id —
  outbound rows, and inbound from platforms whose payloads carry none,
* it is scoped per tenant and per platform, so one customer's ids cannot
  collide with another's,
* and ingestion survives losing the race: with the cheap check disabled, a
  second delivery is absorbed by the constraint, leaves one row, and does not
  take the rest of the batch down with it.

HOW TO RUN:
    python -m pytest team_inbox/tests/test_inbound_idempotency.py -v
"""

from __future__ import annotations

import datetime
import importlib

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor

from wa.tests.meta_path import (
    APP_SECRET,
    FakeGraph,
    inbound_envelope,
    messages_value,
    meta_wa_app,
    run_webhooks_in_process,
    sign_meta_webhook,
    tenant,
    text_message,
)

pytestmark = pytest.mark.django_db

MIGRATION = "team_inbox.migrations.0012_messages_provider_message_id"

WAMID = "wamid.HBgLMjc4MjEyMzQ1NjcVAgASGBQzQTAwMDAwMDAwMDAwMDAwMDAwMAA="


def _row(owner, **overrides):
    """One inbox row, minimal but schema-valid."""
    from team_inbox.models import AuthorChoices, MessageDirectionChoices, MessagePlatformChoices, Messages

    fields = {
        "tenant": owner,
        "content": {"type": "text", "body": {"text": "hi"}},
        "direction": MessageDirectionChoices.INCOMING,
        "platform": MessagePlatformChoices.WHATSAPP,
        "author": AuthorChoices.CONTACT,
    }
    fields.update(overrides)
    return Messages.objects.create(**fields)


# ─────────────────────────────────────────────────────────────────────────────
# The constraint
# ─────────────────────────────────────────────────────────────────────────────


def test_the_database_refuses_a_second_row_with_the_same_provider_message_id():
    """The guarantee, asserted where it lives rather than in the caller.

    A code-level ``exists()`` check cannot promise this: two workers handling
    concurrent redeliveries of one message both read an empty table and both
    write. Only the index can say no to the second write.
    """
    owner = tenant("Idem")
    _row(owner, provider_message_id=WAMID)

    with pytest.raises(IntegrityError), transaction.atomic():
        _row(owner, provider_message_id=WAMID)


def test_rows_with_no_provider_message_id_are_left_alone():
    """Most rows in this table have no provider id and must stay writable.

    Outbound rows have none, and neither do inbound rows from platforms whose
    payload carries no stable identifier — which is why the column is nullable
    and the index carries a predicate instead of the column carrying
    ``unique=True``. NULL and "" are both excluded explicitly: Postgres treats
    NULLs as distinct so they would pass either way, but the empty string is a
    value like any other and a second one would collide.
    """
    from team_inbox.models import AuthorChoices, MessageDirectionChoices, MessagePlatformChoices, Messages

    owner = tenant("Idem")

    for _ in range(2):
        _row(
            owner,
            direction=MessageDirectionChoices.OUTGOING,
            author=AuthorChoices.USER,
            provider_message_id=None,
        )
        _row(owner, platform=MessagePlatformChoices.SMS, provider_message_id="")

    assert Messages.objects.filter(tenant=owner).count() == 4


def test_the_same_provider_id_is_allowed_for_another_tenant_or_platform():
    """Scoped to (tenant, platform), not global.

    A wamid is unique within Meta, but a cross-tenant clash — a shared number,
    a re-imported history, a fixture — should not make one customer's
    ingestion fail on another customer's row.
    """
    from team_inbox.models import MessagePlatformChoices, Messages

    mine = tenant("Mine")
    theirs = tenant("Theirs")

    _row(mine, provider_message_id=WAMID)
    _row(theirs, provider_message_id=WAMID)
    _row(mine, platform=MessagePlatformChoices.SMS, provider_message_id=WAMID)

    assert Messages.objects.filter(provider_message_id=WAMID).count() == 3


# ─────────────────────────────────────────────────────────────────────────────
# Ingestion when it loses the race
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def app():
    return meta_wa_app(tenant("Race"), access_token="race-tenant-token")


@pytest.fixture(autouse=True)
def _meta_env(settings, tmp_path):
    settings.META_APP_SECRET = APP_SECRET
    settings.META_PERM_TOKEN = ""
    settings.MEDIA_ROOT = str(tmp_path / "media")
    run_webhooks_in_process(settings)


@pytest.fixture()
def graph(monkeypatch):
    return FakeGraph().install(monkeypatch)


def test_a_lost_race_is_absorbed_and_costs_nothing_else(client, app, graph, monkeypatch):
    """Two workers both pass the pre-check; the inbox still gains one row.

    ``_existing_inbound`` is stubbed to see nothing, which is exactly what a
    worker sees when a concurrent redelivery has not committed yet. Ingestion
    then runs head-first into the constraint, and has to treat that as "the
    other worker got there" rather than as an error — the second message in
    the same delivery must still land, because one duplicate must not drop a
    batch.
    """
    from team_inbox.models import Messages
    from wa import tasks as wa_tasks
    from wa.models import WAWebhookEvent

    first = inbound_envelope(app, messages_value(app, text_message("wamid.race", "help me")))
    sign_meta_webhook(client, first)

    monkeypatch.setattr(wa_tasks, "_existing_inbound", lambda *args, **kwargs: None)

    again = inbound_envelope(
        app,
        messages_value(
            app,
            text_message("wamid.race", "help me"),
            text_message("wamid.fresh", "and one more"),
        ),
    )
    sign_meta_webhook(client, again)

    bodies = sorted(m.content["body"]["text"] for m in Messages.objects.filter(tenant=app.tenant))
    assert bodies == ["and one more", "help me"], "the duplicate was written, or it took its sibling with it"
    assert WAWebhookEvent.objects.filter(wa_app=app).count() == 2


# ─────────────────────────────────────────────────────────────────────────────
# The backfill in migration 0012
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def backfill(db):
    """Migration 0012's data pass, run against its own historical model.

    Following ``tenants/tests/test_bsp_credentials_encryption.py``: the
    function is called with the historical ``apps`` a ``RunPython`` really
    gets, so the test sees the plain ``save()``/``bulk_update()`` of that state
    rather than the live model's behaviour.
    """
    historical = (
        MigrationExecutor(connection).loader.project_state([("team_inbox", "0012_messages_provider_message_id")]).apps
    )
    migration = importlib.import_module(MIGRATION)
    return lambda: migration.backfill_provider_message_id(historical, None)


def _pre_migration_row(owner, wamid, **overrides):
    """A row as it stood before 0012: the id in the JSON, the column NULL."""
    content = {"type": "text", "body": {"text": "hi"}}
    if wamid is not None:
        content["_meta"] = {"webhook_event_id": "evt-1", "wa_message_id": wamid}
    row = _row(owner, content=content, provider_message_id=None, **overrides)
    return row


def test_the_backfill_copies_the_id_out_of_the_json(backfill):
    """Rows written before the column existed still get their key.

    Without this the constraint would guard only new traffic, and every inbox
    row already on disk would stay redeliverable forever.
    """
    from team_inbox.models import AuthorChoices, MessageDirectionChoices, MessagePlatformChoices

    owner = tenant("Backfill")
    inbound = _pre_migration_row(owner, WAMID)
    outbound = _pre_migration_row(
        owner,
        "wamid.OUT",
        direction=MessageDirectionChoices.OUTGOING,
        author=AuthorChoices.USER,
    )
    other_platform = _pre_migration_row(owner, "tg-771", platform=MessagePlatformChoices.TELEGRAM)
    no_meta = _pre_migration_row(owner, None)

    backfill()

    inbound.refresh_from_db()
    outbound.refresh_from_db()
    other_platform.refresh_from_db()
    no_meta.refresh_from_db()

    assert inbound.provider_message_id == WAMID
    # Left alone on purpose: the column is an *inbound* idempotency key, and
    # filling it for rows the constraint has no business policing would only
    # create ways for a legitimate write to fail.
    assert outbound.provider_message_id is None
    assert other_platform.provider_message_id is None
    assert no_meta.provider_message_id is None


def test_the_backfill_survives_duplicates_already_on_disk_and_a_second_run(backfill):
    """The bug may have written duplicates before the fix landed.

    Both copies stay in the inbox — a migration does not delete a customer's
    messages — but only the older one claims the id, so the constraint can be
    created and the next redelivery of that wamid is recognised. Running the
    pass twice must not then hand the same id to the younger copy.
    """
    from team_inbox.models import Messages

    owner = tenant("Backfill")
    older = _pre_migration_row(owner, WAMID)
    younger = _pre_migration_row(owner, WAMID)
    # ``timestamp`` is auto_now_add, so the order has to be stated rather than
    # assumed from creation order.
    stamp = datetime.datetime(2026, 3, 1, 9, 0, tzinfo=datetime.timezone.utc)
    Messages.objects.filter(pk=older.pk).update(timestamp=stamp)
    Messages.objects.filter(pk=younger.pk).update(timestamp=stamp + datetime.timedelta(minutes=5))

    backfill()
    backfill()

    older.refresh_from_db()
    younger.refresh_from_db()
    assert older.provider_message_id == WAMID, "the oldest copy owns the id"
    assert younger.provider_message_id is None, "a duplicate must not claim an id another row holds"
    assert Messages.objects.filter(tenant=owner).count() == 2, "the backfill deleted a message"
