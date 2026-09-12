"""The WhatsApp app's opaque webhook identifier (#310) — the column itself.

``wa/tests/test_webhook_app_identity.py`` covers the receiver, the URLs and the
setup endpoint. This file covers the property the whole scheme rests on: that
the identifier in an app's callback URL is *issued*, unguessable, unique, and
not a function of anything else about the app.

Why that matters rather than being pedantry about randomness:

* derived from the primary key, it would be a small integer anyone can count to,
  and the receiver is public and unauthenticated;
* derived from the tenant, the WABA or the number, it would be **reproduced** by
  deleting an app and creating another like it — so a client's stale dashboard
  entry would start delivering into a different app's receiver, silently;
* derived from anything at all, the URL would leak that thing to whoever can
  read it, and a callback URL is pasted into third-party dashboards and support
  tickets.

The migration's backfill is exercised here too, because "existing apps keep
working" is a promise about rows that already exist, and the only place that
promise is kept is in ``0029``.

HOW TO RUN:
    .venv/bin/python -m pytest tenants/tests/test_wa_webhook_identifier.py -v
"""

from __future__ import annotations

import inspect
import types
import uuid
from importlib import import_module

import pytest
from django.db import IntegrityError, connection, transaction

from tenants.models import (
    WA_WEBHOOK_IDENTIFIER_PREFIX,
    Tenant,
    TenantWAApp,
    generate_wa_webhook_identifier,
    mask_wa_webhook_identifier,
)

MIGRATION = "tenants.migrations.0029_wa_app_webhook_identifier"


def _tenant(prefix: str = "WhkTenant") -> Tenant:
    return Tenant.objects.create(name=f"{prefix}-{uuid.uuid4().hex[:8]}", is_active=True)


def _app_kwargs(tenant, **overrides) -> dict:
    fields = {
        "tenant": tenant,
        "app_name": f"app-{uuid.uuid4().hex[:6]}",
        "app_id": f"gs-{uuid.uuid4().hex[:8]}",
        "app_secret": "s",
        "wa_number": f"+1{uuid.uuid4().int % 10**10:010d}",
        "waba_id": f"waba-{uuid.uuid4().hex[:8]}",
        "phone_number_id": f"pn-{uuid.uuid4().hex[:8]}",
        "bsp": "META",
    }
    fields.update(overrides)
    return fields


def _app(tenant=None, **overrides) -> TenantWAApp:
    return TenantWAApp.objects.create(**_app_kwargs(tenant or _tenant(), **overrides))


# ─────────────────────────────────────────────────────────────────────────────
# Issued on creation
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_an_identifier_is_issued_when_an_app_is_created():
    app = _app()

    assert app.webhook_identifier
    assert app.webhook_identifier.startswith(WA_WEBHOOK_IDENTIFIER_PREFIX)
    # 24 random bytes is ~192 bits; anything much shorter is guessable at the
    # rate a public endpoint can be hit.
    assert len(app.webhook_identifier) >= 32

    app.refresh_from_db()
    assert app.webhook_identifier, "the value has to be persisted, not only set in memory"


@pytest.mark.django_db
def test_every_app_gets_a_different_identifier():
    identifiers = {_app().webhook_identifier for _ in range(20)}

    assert len(identifiers) == 20


@pytest.mark.django_db
def test_the_identifier_is_not_derived_from_anything_about_the_app():
    """Two apps identical in every way that could be used to derive one."""
    tenant = _tenant()
    shared = {"waba_id": "waba-shared-310", "app_id": "gs-shared-310", "app_name": "same-name"}

    first = _app(tenant, phone_number_id="pn-shared-310", **shared)
    second = _app(tenant, phone_number_id="pn-shared-310", **shared)

    assert first.webhook_identifier != second.webhook_identifier

    # Long, distinctive values only. The primary key and the tenant id are
    # *also* things the identifier must not be derived from, but they are small
    # integers: a substring check for "7" in a random 32-character string is
    # true about nine times in ten, so the assertion would be noise that fails
    # on whichever pk the suite happens to reach. The structural check below is
    # what rules them out, and rules out everything else with them.
    for app in (first, second):
        body = app.webhook_identifier
        for derivable in (app.waba_id, app.phone_number_id, app.app_id, app.wa_number.lstrip("+")):
            assert derivable not in body, f"{derivable!r} must not be recoverable from the identifier"

    # The generator is handed nothing — not the row, not the tenant, not the
    # number — so there is nothing about an app it *could* encode. That is a
    # stronger statement than any search through the output, and it is the one
    # a future "make the URL friendlier by putting the tenant in it" change
    # would have to break first.
    assert list(inspect.signature(generate_wa_webhook_identifier).parameters) == []


@pytest.mark.django_db
def test_an_identifier_is_not_reused_after_its_app_is_deleted():
    """Acceptance. A reused identifier means a client's old dashboard entry
    starts delivering into whatever app took the URL over."""
    tenant = _tenant()
    kwargs = _app_kwargs(tenant)

    original = TenantWAApp.objects.create(**kwargs)
    retired = original.webhook_identifier
    original.delete()

    # Recreated as identically as the database allows: same tenant, same number,
    # same WABA, same Gupshup app id.
    replacement = TenantWAApp.objects.create(**kwargs)

    assert replacement.webhook_identifier != retired
    assert not TenantWAApp.objects.filter(webhook_identifier=retired).exists()


@pytest.mark.django_db
def test_the_database_refuses_a_duplicate_identifier():
    """The uniqueness is a constraint, not a convention: resolution is a single
    lookup that takes the one row, and two rows would make it arbitrary."""
    first = _app()

    with pytest.raises(IntegrityError), transaction.atomic():
        TenantWAApp.objects.create(**_app_kwargs(_tenant(), webhook_identifier=first.webhook_identifier))


@pytest.mark.django_db
def test_bulk_create_gets_identifiers_too():
    """``bulk_create`` never calls ``save``, which is why the generator is the
    field's ``default`` rather than only a line in ``save``."""
    tenant = _tenant()

    created = TenantWAApp.objects.bulk_create([TenantWAApp(**_app_kwargs(tenant)) for _ in range(3)])

    identifiers = {app.webhook_identifier for app in created}
    assert len(identifiers) == 3
    assert all(i and i.startswith(WA_WEBHOOK_IDENTIFIER_PREFIX) for i in identifiers)


@pytest.mark.django_db
def test_a_blanked_identifier_is_reissued_on_save():
    """The safety net for rows the field default cannot reach — a fixture
    written before #310, or a caller that set the column to "". An app with no
    identifier has no callback URL, and nothing else would say so."""
    app = _app()
    TenantWAApp.objects.filter(pk=app.pk).update(webhook_identifier="")

    reloaded = TenantWAApp.objects.get(pk=app.pk)
    reloaded.save()

    reloaded.refresh_from_db()
    assert reloaded.webhook_identifier


@pytest.mark.django_db
def test_a_targeted_save_still_persists_a_reissued_identifier():
    """``save(update_fields=[...])`` would otherwise write the other column and
    drop the reissued identifier on the floor — the same trap
    ``_absorb_bsp_secrets`` widens ``update_fields`` for."""
    app = _app()
    TenantWAApp.objects.filter(pk=app.pk).update(webhook_identifier="")

    reloaded = TenantWAApp.objects.get(pk=app.pk)
    reloaded.daily_limit = 4321
    reloaded.save(update_fields=["daily_limit"])

    reloaded.refresh_from_db()
    assert reloaded.daily_limit == 4321
    assert reloaded.webhook_identifier


@pytest.mark.django_db
def test_absorbing_a_legacy_credential_does_not_disturb_the_identifier():
    """#289's ``bsp_credentials`` absorption and this both widen
    ``update_fields`` in the same ``save``; neither may cancel the other."""
    app = _app()
    issued = app.webhook_identifier

    app.bsp_credentials = {"access_token": "rotated-token"}
    app.save(update_fields=["bsp_credentials"])

    app.refresh_from_db()
    assert app.bsp_access_token == "rotated-token"
    assert app.webhook_identifier == issued


# ─────────────────────────────────────────────────────────────────────────────
# The generator and the mask, directly
# ─────────────────────────────────────────────────────────────────────────────


def test_the_generator_never_repeats_itself():
    assert len({generate_wa_webhook_identifier() for _ in range(1000)}) == 1000


def test_the_generated_value_is_url_path_safe():
    """It goes into a URL path, so a character needing escaping would make the
    URL a client pastes differ from the URL the receiver matches."""
    for _ in range(50):
        value = generate_wa_webhook_identifier()
        assert "/" not in value
        assert "%" not in value
        assert value == value.strip()


def test_the_mask_shows_a_prefix_and_no_more():
    value = generate_wa_webhook_identifier()

    hint = mask_wa_webhook_identifier(value)

    assert value.startswith(hint.rstrip("…"))
    assert hint != value
    assert len(hint.rstrip("…")) == 12
    assert mask_wa_webhook_identifier("") == ""
    assert mask_wa_webhook_identifier(None) == ""


# ─────────────────────────────────────────────────────────────────────────────
# The migration backfills rows that already exist
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_the_migration_backfills_every_existing_app():
    """Acceptance: every existing app gets an identifier, each its own.

    The column is already ``NOT NULL`` by the time tests run, so the pre-0029
    state is reconstructed by dropping that constraint and nulling the rows —
    both inside the test's transaction, which rolls back. The migration's
    ``RunPython`` body is then called with the *historical* model, not the live
    one, so its ``save`` cannot quietly do the backfill's job for it.
    """
    from django.db.migrations.executor import MigrationExecutor

    tenant = _tenant()
    apps = [_app(tenant) for _ in range(3)]

    with connection.cursor() as cursor:
        # The rows above were inserted in this same transaction, so their
        # deferred foreign-key triggers are still pending and Postgres refuses to
        # ALTER the table ("pending trigger events") until they have fired.
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        cursor.execute("ALTER TABLE tenants_tenantwaapp ALTER COLUMN webhook_identifier DROP NOT NULL")
        cursor.execute("UPDATE tenants_tenantwaapp SET webhook_identifier = NULL")

    assert TenantWAApp.objects.filter(webhook_identifier__isnull=True).count() == 3

    state = MigrationExecutor(connection).loader.project_state(("tenants", "0029_wa_app_webhook_identifier"))
    historical = state.apps.get_model("tenants", "TenantWAApp")
    assert historical is not TenantWAApp

    migration = import_module(MIGRATION)
    migration.fill_webhook_identifiers(
        types.SimpleNamespace(get_model=lambda *_args, **_kwargs: historical),
        connection.schema_editor(),
    )

    filled = list(TenantWAApp.objects.filter(pk__in=[a.pk for a in apps]).values_list("webhook_identifier", flat=True))
    assert all(value for value in filled)
    assert len(set(filled)) == 3
    assert all(value.startswith(WA_WEBHOOK_IDENTIFIER_PREFIX) for value in filled)


@pytest.mark.django_db
def test_the_migration_leaves_an_app_that_already_has_one_alone():
    """Re-running the backfill must not rotate a live identifier: the client's
    registered URL would stop working, which is exactly what #310 promises not
    to do."""
    app = _app()
    issued = app.webhook_identifier

    migration = import_module(MIGRATION)
    migration.fill_webhook_identifiers(
        types.SimpleNamespace(get_model=lambda *_args, **_kwargs: TenantWAApp),
        connection.schema_editor(),
    )

    app.refresh_from_db()
    assert app.webhook_identifier == issued
