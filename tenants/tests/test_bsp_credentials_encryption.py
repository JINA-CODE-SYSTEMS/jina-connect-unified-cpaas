"""WhatsApp BSP secrets encrypted at rest (#289).

``TenantWAApp.bsp_credentials`` was a plain ``JSONField`` holding a live Meta
access token — readable in the database, in every backup and on every replica,
for every organisation at once. Seven other models in this project already keep
provider credentials in ``EncryptedTextField``; ``meta.MetaBusinessConnection``
stores the *same* Meta token, for the same provider, encrypted. WhatsApp, the
primary channel, was the only one that did not.

What these tests pin:

* the token is not recoverable from a raw ``SELECT *`` on the row,
* a token written the old way — inside ``bsp_credentials`` — does not come to
  rest in that plaintext column, because clients still send it that way and
  dropping the shape would break them,
* non-secret configuration (``waba_id``) is still kept in the JSON, which is
  why the column survives at all,
* every reader resolves the token from the encrypted column,
* migration 0028 carries existing tokens across, and reversing carries them
  back, so nobody has to re-enter one in either direction,
* no lookup by token value exists or can be made to work, Fernet ciphertext
  being non-deterministic.

HOW TO RUN:
    DB_NAME=... python -m pytest tenants/tests/test_bsp_credentials_encryption.py -v
"""

from __future__ import annotations

import importlib

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, override_settings
from encrypted_model_fields.fields import EncryptedTextField

from tenants.models import Tenant, TenantWAApp

MIGRATION = "tenants.migrations.0028_encrypt_wa_app_bsp_secrets"

TOKEN = "EAAG-live-meta-access-token"
PARTNER_TOKEN = "gs-partner-app-token"


def _wa_app(tenant, number="+14155550100", **overrides):
    fields = {
        "tenant": tenant,
        "app_name": "creds-app",
        "app_id": "GUPSHUP-APP-ID",
        "app_secret": "gupshup-app-secret",
        "wa_number": number,
        "waba_id": "waba-1",
        "phone_number_id": "pn-1",
        "bsp": "META",
        "is_active": True,
    }
    fields.update(overrides)
    return TenantWAApp.objects.create(**fields)


def _raw_row(pk):
    """Every column of the row as a database dump would hand it over."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT * FROM tenants_tenantwaapp WHERE id = %s", [pk])
        columns = [c[0] for c in cursor.description]
        row = cursor.fetchone()
    return dict(zip(columns, row))


class BspSecretsAtRestTests(TestCase):
    """What a database dump gives up."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Creds Tenant")

    def test_the_token_appears_in_no_column(self):
        """#289's acceptance criterion: unreadable in a raw SELECT."""
        app = _wa_app(self.tenant, bsp_access_token=TOKEN, bsp_partner_app_token=PARTNER_TOKEN)

        for name, value in _raw_row(app.pk).items():
            if isinstance(value, (str, bytes, memoryview)):
                text = value if isinstance(value, str) else bytes(value).decode("utf-8", "replace")
                self.assertNotIn(TOKEN, text, f"column {name} still carries the access token")
                self.assertNotIn(PARTNER_TOKEN, text, f"column {name} still carries the partner token")

    def test_the_application_still_reads_its_own_token_back(self):
        """Encrypted at rest is only useful if the holder of the key can read it."""
        app = _wa_app(self.tenant, bsp_access_token=TOKEN)

        app.refresh_from_db()

        self.assertEqual(app.bsp_access_token, TOKEN)
        self.assertEqual(TenantWAApp.objects.get(pk=app.pk).bsp_access_token, TOKEN)

    def test_the_stored_ciphertext_is_not_the_token(self):
        app = _wa_app(self.tenant, bsp_access_token=TOKEN)

        stored = _raw_row(app.pk)["bsp_access_token"]

        self.assertNotEqual(stored, TOKEN)
        self.assertTrue(str(stored).startswith("gAAAAA"), stored)

    def test_an_app_with_no_token_stores_no_ciphertext(self):
        """The common case — an app on the global token — must not be forced to
        carry a meaningless encrypted blank that reads back as a token."""
        app = _wa_app(self.tenant)

        self.assertEqual(app.bsp_access_token, "")
        self.assertEqual(app.bsp_partner_app_token, "")

    def test_every_bsp_secret_field_is_encrypted(self):
        """The guard for #311 and anything after it: a secret added to this
        model must be an ``EncryptedTextField``, not another JSON key."""
        for field_name in TenantWAApp._BSP_SECRET_FIELDS.values():
            field = TenantWAApp._meta.get_field(field_name)
            self.assertIsInstance(field, EncryptedTextField, f"{field_name} is not encrypted at rest")

    def test_the_model_keeps_no_history_table_to_leak_into(self):
        """``simple_history`` copies every tracked field into a second table. If
        this model ever gains ``HistoricalRecords`` the copy will be of the
        encrypted columns — there is no plaintext token left for it to take."""
        self.assertFalse(hasattr(TenantWAApp, "history"))


class BspCredentialsJsonTests(TestCase):
    """The plaintext JSON column, and what may no longer live in it."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Json Tenant")

    def test_a_token_written_into_the_json_is_moved_out_of_it(self):
        """Clients have sent ``bsp_credentials={"access_token": …}`` since #275.
        That keeps working — but the value does not stay in the JSON."""
        app = _wa_app(self.tenant, bsp_credentials={"access_token": TOKEN})

        # The leak first: what the plaintext column holds after the write.
        self.assertNotIn(
            TOKEN,
            str(_raw_row(app.pk)["bsp_credentials"]),
            "the access token is readable in the plaintext bsp_credentials column",
        )
        self.assertEqual(app.bsp_access_token, TOKEN)
        self.assertEqual(app.bsp_credentials, {})

    def test_a_partner_token_written_into_the_json_is_moved_out_of_it(self):
        app = _wa_app(self.tenant, bsp="GUPSHUP", bsp_credentials={"partner_app_token": PARTNER_TOKEN})

        self.assertEqual(app.bsp_partner_app_token, PARTNER_TOKEN)
        self.assertEqual(app.bsp_credentials, {})

    def test_non_secret_configuration_is_left_in_the_json(self):
        """``wa.services.template_sync`` still falls back to ``waba_id`` here,
        which is why the column is kept rather than dropped."""
        app = _wa_app(self.tenant, bsp_credentials={"access_token": TOKEN, "waba_id": "waba-from-json"})

        app.refresh_from_db()

        self.assertEqual(app.bsp_credentials, {"waba_id": "waba-from-json"})
        self.assertEqual(app.bsp_access_token, TOKEN)

    def test_a_targeted_save_still_moves_the_token(self):
        """``save(update_fields=["bsp_credentials"])`` would otherwise write the
        stripped JSON and drop the token on the floor."""
        app = _wa_app(self.tenant)

        app.bsp_credentials = {"access_token": TOKEN}
        app.save(update_fields=["bsp_credentials"])

        app.refresh_from_db()
        self.assertEqual(app.bsp_access_token, TOKEN)
        self.assertEqual(app.bsp_credentials, {})

    def test_rotating_through_the_json_replaces_the_stored_token(self):
        """A tenant rotating a token the old way must actually rotate it."""
        app = _wa_app(self.tenant, bsp_access_token="old-token")

        app.bsp_credentials = {"access_token": "new-token"}
        app.save()

        app.refresh_from_db()
        self.assertEqual(app.bsp_access_token, "new-token")

    def test_an_unrelated_save_does_not_blank_a_live_token(self):
        app = _wa_app(self.tenant, bsp_access_token=TOKEN)

        app.bsp_credentials = {"waba_id": "waba-2"}
        app.save()
        app.daily_limit = 5000
        app.save()

        app.refresh_from_db()
        self.assertEqual(app.bsp_access_token, TOKEN)

    def test_an_empty_value_in_the_json_does_not_blank_a_live_token(self):
        """A client echoing back ``{"access_token": ""}`` is not a revocation."""
        app = _wa_app(self.tenant, bsp_access_token=TOKEN)

        app.bsp_credentials = {"access_token": ""}
        app.save()

        app.refresh_from_db()
        self.assertEqual(app.bsp_access_token, TOKEN)
        self.assertEqual(app.bsp_credentials, {})

    def test_a_null_json_column_is_left_alone(self):
        app = _wa_app(self.tenant, bsp_credentials=None)

        app.save()

        self.assertIsNone(app.bsp_credentials)

    def test_the_dict_the_caller_passed_is_not_mutated(self):
        supplied = {"access_token": TOKEN}

        _wa_app(self.tenant, bsp_credentials=supplied)

        self.assertEqual(supplied, {"access_token": TOKEN})


class BspSecretLookupTests(TestCase):
    """Why nothing may look an app up by its token."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Lookup Tenant")

    def test_the_same_token_encrypts_differently_on_two_apps(self):
        a = _wa_app(self.tenant, number="+14155550201", bsp_access_token=TOKEN)
        b = _wa_app(self.tenant, number="+14155550202", bsp_access_token=TOKEN)

        self.assertNotEqual(_raw_row(a.pk)["bsp_access_token"], _raw_row(b.pk)["bsp_access_token"])

    def test_filtering_by_token_value_finds_nothing(self):
        """Fernet output is non-deterministic, so an equality filter on this
        column can never match. Nothing in the codebase resolves an app this
        way — ``waba_id`` and ``phone_number_id`` are what webhooks route on —
        and this pins that it must stay that way."""
        app = _wa_app(self.tenant, bsp_access_token=TOKEN)

        self.assertTrue(TenantWAApp.objects.filter(pk=app.pk).exists())
        self.assertFalse(TenantWAApp.objects.filter(bsp_access_token=TOKEN).exists())


class BspSecretReaderTests(TestCase):
    """Every place that resolved a token out of the JSON."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Reader Tenant")

    @override_settings(META_PERM_TOKEN="global-platform-token")
    def test_the_meta_adapter_prefers_the_encrypted_per_app_token(self):
        from wa.adapters.meta_direct import MetaDirectAdapter

        app = _wa_app(self.tenant, bsp_access_token=TOKEN)

        self.assertEqual(MetaDirectAdapter(app)._resolve_access_token(), TOKEN)

    @override_settings(META_PERM_TOKEN="global-platform-token")
    def test_the_meta_adapter_still_falls_back_to_the_global_token(self):
        from wa.adapters.meta_direct import MetaDirectAdapter

        app = _wa_app(self.tenant)

        self.assertEqual(MetaDirectAdapter(app)._resolve_access_token(), "global-platform-token")

    def test_the_gupshup_adapter_prefers_the_encrypted_partner_token(self):
        from wa.adapters.gupshup import GupshupAdapter

        app = _wa_app(self.tenant, bsp="GUPSHUP", bsp_partner_app_token=PARTNER_TOKEN)

        self.assertEqual(GupshupAdapter(app)._resolve_partner_token(), PARTNER_TOKEN)

    def test_the_gupshup_adapter_still_falls_back_to_app_secret(self):
        from wa.adapters.gupshup import GupshupAdapter

        app = _wa_app(self.tenant, bsp="GUPSHUP")

        self.assertEqual(GupshupAdapter(app)._resolve_partner_token(), "gupshup-app-secret")

    @override_settings(META_PERM_TOKEN="global-platform-token")
    def test_template_sync_reads_the_per_app_token(self):
        """``get_meta_access_token`` tested ``meta_access_token``, a field no
        model ever declared, so template sync ran on the global token even for
        a tenant that had configured its own."""
        from wa.services.meta_template_service import get_meta_access_token

        app = _wa_app(self.tenant, bsp_access_token=TOKEN)

        self.assertEqual(get_meta_access_token(app), TOKEN)

    def test_the_order_service_reads_the_per_app_token(self):
        from wa.services.order_service import OrderService

        app = _wa_app(self.tenant, bsp_access_token=TOKEN)

        self.assertEqual(OrderService._get_access_token(app), TOKEN)

    @override_settings(META_PERM_TOKEN=None)
    def test_a_missing_token_names_the_field_and_not_a_value(self):
        """The error an operator sees has to point at the right knob, and must
        not carry a credential into a log or an exception report."""
        from django.core.exceptions import ValidationError

        from wa.services.order_service import OrderService

        app = _wa_app(self.tenant)

        with self.assertRaises(ValidationError) as caught:
            OrderService._get_access_token(app)

        self.assertIn("bsp_access_token", str(caught.exception))

    def test_no_reader_resolves_a_token_out_of_the_json_any_more(self):
        """The token lives in one place now. A reader still reaching into
        ``bsp_credentials`` would quietly keep a plaintext path alive."""
        app = _wa_app(self.tenant)
        TenantWAApp.objects.filter(pk=app.pk).update(bsp_credentials={"access_token": "stale-json-token"})
        app.refresh_from_db()

        from wa.adapters.meta_direct import MetaDirectAdapter
        from wa.services.meta_template_service import get_meta_access_token

        with override_settings(META_PERM_TOKEN="global-platform-token"):
            self.assertEqual(MetaDirectAdapter(app)._resolve_access_token(), "global-platform-token")
            self.assertEqual(get_meta_access_token(app), "global-platform-token")


class BspSecretBackfillTests(TestCase):
    """Migration 0028's data carry — what every configured tenant depends on."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # The real historical model for the state 0028 leaves behind: same
        # columns, and the plain ``save()`` a RunPython actually gets rather
        # than the live model's, which would absorb the secret right back out
        # of the JSON and hide what the backfill did.
        executor = MigrationExecutor(connection)
        cls.historical_apps = executor.loader.project_state([("tenants", "0028_encrypt_wa_app_bsp_secrets")]).apps
        cls.migration = importlib.import_module(MIGRATION)

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Backfill Tenant")

    def _pre_migration_row(self, creds, number="+14155550300"):
        """A row as it stood before 0028: secret in the JSON, columns blank."""
        app = _wa_app(self.tenant, number=number)
        TenantWAApp.objects.filter(pk=app.pk).update(
            bsp_credentials=creds,
            bsp_access_token="",
            bsp_partner_app_token="",
        )
        app.refresh_from_db()
        return app

    def test_an_existing_token_is_carried_into_the_encrypted_column(self):
        """Nobody has to re-enter a token."""
        app = self._pre_migration_row({"access_token": TOKEN})

        self.migration.move_secrets_into_encrypted_columns(self.historical_apps, None)

        app.refresh_from_db()
        self.assertEqual(app.bsp_access_token, TOKEN)
        self.assertEqual(app.bsp_credentials, {})
        self.assertNotIn(TOKEN, str(_raw_row(app.pk)["bsp_credentials"]))

    def test_both_providers_secrets_are_carried(self):
        app = self._pre_migration_row({"access_token": TOKEN, "partner_app_token": PARTNER_TOKEN})

        self.migration.move_secrets_into_encrypted_columns(self.historical_apps, None)

        app.refresh_from_db()
        self.assertEqual(app.bsp_access_token, TOKEN)
        self.assertEqual(app.bsp_partner_app_token, PARTNER_TOKEN)

    def test_non_secret_configuration_survives_the_backfill(self):
        app = self._pre_migration_row({"access_token": TOKEN, "waba_id": "waba-json"})

        self.migration.move_secrets_into_encrypted_columns(self.historical_apps, None)

        app.refresh_from_db()
        self.assertEqual(app.bsp_credentials, {"waba_id": "waba-json"})

    def test_a_row_with_nothing_secret_is_untouched(self):
        nothing = self._pre_migration_row(None, number="+14155550301")
        config_only = self._pre_migration_row({"waba_id": "waba-json"}, number="+14155550302")

        self.migration.move_secrets_into_encrypted_columns(self.historical_apps, None)

        nothing.refresh_from_db()
        config_only.refresh_from_db()
        self.assertIsNone(nothing.bsp_credentials)
        self.assertEqual(config_only.bsp_credentials, {"waba_id": "waba-json"})
        self.assertEqual(nothing.bsp_access_token, "")

    def test_reversing_puts_the_token_back_so_a_rollback_keeps_sending(self):
        """Unlike #301's one-way digest, this secret is recoverable — so a
        rollback must recover it instead of stranding every tenant."""
        app = self._pre_migration_row({"access_token": TOKEN, "waba_id": "waba-json"})

        self.migration.move_secrets_into_encrypted_columns(self.historical_apps, None)
        self.migration.move_secrets_back_into_json(self.historical_apps, None)

        app.refresh_from_db()
        self.assertEqual(app.bsp_credentials, {"waba_id": "waba-json", "access_token": TOKEN})

    def test_reversing_an_app_with_no_secret_writes_nothing(self):
        app = self._pre_migration_row(None)

        self.migration.move_secrets_back_into_json(self.historical_apps, None)

        app.refresh_from_db()
        self.assertIsNone(app.bsp_credentials)
