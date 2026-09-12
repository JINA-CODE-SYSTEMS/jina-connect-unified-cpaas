"""Tests for tenant access keys at rest, and for rotation and revocation (#301).

An access key is a bearer credential: presented in ``X-ACCESS-KEY`` it names the
tenant a JWT is scoped to, and presented to an MCP tool it is the whole
credential. It used to sit in the database in plaintext, with no way to rotate or
revoke it, so any copy of the database carried a working key for every
organisation — the same finding as #289.

Run:
    DB_NAME=... .venv/bin/python -m pytest tenants/tests/test_access_key_hashing.py
"""

import importlib

from django.db import connection
from django.test import TestCase, override_settings
from django.utils.crypto import salted_hmac
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.test import APIRequestFactory

from tenants.authentication import TenantAccessKeyAuthentication, tenant_from_access_key
from tenants.models import Tenant, TenantAccessKey


class AccessKeyAtRestTests(TestCase):
    """What a database dump gives up."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Key Tenant")
        cls.key_obj, cls.raw_key = TenantAccessKey.issue(cls.tenant)

    def test_raw_key_appears_in_no_column(self):
        """#301: the acceptance criterion — unreadable in a raw SELECT."""
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM tenants_tenantaccesskey WHERE id = %s", [self.key_obj.pk])
            columns = [c[0] for c in cursor.description]
            row = cursor.fetchone()

        stored = {name: value for name, value in zip(columns, row)}
        for name, value in stored.items():
            if isinstance(value, str):
                self.assertNotIn(self.raw_key, value, f"column {name} still carries the key")

    def test_only_the_digest_is_stored(self):
        reloaded = TenantAccessKey.objects.get(pk=self.key_obj.pk)
        self.assertEqual(reloaded.key_hash, TenantAccessKey.hash_key(self.raw_key))
        self.assertNotEqual(reloaded.key_hash, self.raw_key)

    def test_prefix_identifies_the_key_without_revealing_it(self):
        """An operator needs to name a key in a log line or an admin list."""
        self.assertTrue(self.raw_key.startswith(self.key_obj.key_prefix))
        self.assertEqual(len(self.key_obj.key_prefix), TenantAccessKey.PREFIX_LENGTH)
        self.assertIn(self.key_obj.key_prefix, str(self.key_obj))
        self.assertNotIn(self.raw_key, str(self.key_obj))

    def test_issued_keys_are_unguessable_and_distinct(self):
        _, other_key = TenantAccessKey.issue(self.tenant)
        self.assertNotEqual(other_key, self.raw_key)
        self.assertGreater(len(other_key), 32)

    def test_digest_is_stable_and_key_specific(self):
        self.assertEqual(TenantAccessKey.hash_key(self.raw_key), TenantAccessKey.hash_key(self.raw_key))
        self.assertNotEqual(TenantAccessKey.hash_key(self.raw_key), TenantAccessKey.hash_key(self.raw_key + "x"))


class AccessKeyResolutionTests(TestCase):
    """Lookup by value became lookup by digest — it still has to find the key."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Resolve Tenant")
        cls.key_obj, cls.raw_key = TenantAccessKey.issue(cls.tenant)

    def _request(self, access_key=None):
        headers = {"HTTP_X_ACCESS_KEY": access_key} if access_key else {}
        return APIRequestFactory().post("/token/", **headers)

    def test_issued_key_resolves_to_its_tenant(self):
        self.assertEqual(TenantAccessKey.resolve(self.raw_key).tenant, self.tenant)

    def test_unknown_key_resolves_to_nothing(self):
        self.assertIsNone(TenantAccessKey.resolve("jc_never-issued"))

    def test_authenticator_returns_the_tenant_as_auth(self):
        result = TenantAccessKeyAuthentication().authenticate(self._request(self.raw_key))
        self.assertEqual(result, (None, self.tenant))

    def test_authenticator_defers_when_no_header_is_sent(self):
        """DRF reads None as "nothing to say"; (None, None) set request.user to
        None outright, which is not the same thing (#301)."""
        self.assertIsNone(TenantAccessKeyAuthentication().authenticate(self._request()))

    def test_authenticator_rejects_an_unknown_key(self):
        with self.assertRaises(AuthenticationFailed):
            TenantAccessKeyAuthentication().authenticate(self._request("jc_never-issued"))

    def test_header_helper_returns_none_without_a_header(self):
        self.assertIsNone(tenant_from_access_key(self._request()))

    def test_digest_under_a_fallback_secret_still_resolves(self):
        """SECRET_KEY_FALLBACKS is why the digest honours more than one secret:
        a routine SECRET_KEY rotation must not silently revoke every key."""
        fallback_secret = "an-older-secret-key"
        digest = salted_hmac(
            TenantAccessKey.HASH_SALT, self.raw_key, secret=fallback_secret, algorithm="sha256"
        ).hexdigest()
        TenantAccessKey.objects.filter(pk=self.key_obj.pk).update(key_hash=digest)

        with override_settings(SECRET_KEY_FALLBACKS=[fallback_secret]):
            self.assertEqual(TenantAccessKey.resolve(self.raw_key).tenant, self.tenant)

        # Without the fallback listed, the same row no longer matches.
        with override_settings(SECRET_KEY_FALLBACKS=[]):
            self.assertIsNone(TenantAccessKey.resolve(self.raw_key))


class _LegacyRow:
    """Stands in for a pre-0027 row, which still had a plaintext ``key``."""

    def __init__(self, key):
        self.key = key
        self.key_hash = None
        self.key_prefix = None
        self.updated_fields = None

    def save(self, update_fields=None):
        self.updated_fields = update_fields


class _LegacyRegistry:
    """The narrow slice of ``apps`` a RunPython backfill actually touches."""

    def __init__(self, rows):
        self._rows = rows

    def get_model(self, app_label, model_name):
        registry = self

        class _Objects:
            @staticmethod
            def all():
                return _Objects

            @staticmethod
            def iterator():
                return iter(registry._rows)

        return type("_HistoricalModel", (), {"objects": _Objects})


class AccessKeyBackfillTests(TestCase):
    """Migration 0027's backfill — the piece every live key depends on."""

    def _run_backfill(self, rows):
        # The backfill cannot run against the schema the tests get, because the
        # plaintext column is already gone by then. Its input is stood in for
        # instead; what matters is the digest it writes.
        migration = importlib.import_module("tenants.migrations.0027_tenant_access_key_hashed")
        migration.digest_existing_keys(_LegacyRegistry(rows), None)

    def test_existing_key_is_digested_to_what_resolve_looks_for(self):
        """An access key issued before the migration keeps authenticating."""
        row = _LegacyRow("an-operator-chosen-key")

        self._run_backfill([row])

        self.assertEqual(row.key_hash, TenantAccessKey.hash_key("an-operator-chosen-key"))
        self.assertEqual(row.key_prefix, "an-opera")
        self.assertEqual(row.updated_fields, ["key_hash", "key_prefix"])

    def test_blank_key_gets_a_digest_that_matches_no_key(self):
        """The unique constraint needs a value; nothing should be able to
        present the key it stands for, because there isn't one."""
        row = _LegacyRow("")

        self._run_backfill([row])

        self.assertEqual(len(row.key_hash), 64)
        self.assertNotEqual(row.key_hash, TenantAccessKey.hash_key(""))
        self.assertEqual(row.key_prefix, "")


class AccessKeyRotationTests(TestCase):
    """Rotation and revocation, which the model had neither of."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Rotate Tenant")
        self.key_obj, self.raw_key = TenantAccessKey.issue(self.tenant)

    def test_rotate_replaces_the_secret_in_place(self):
        new_key = self.key_obj.rotate()

        self.assertNotEqual(new_key, self.raw_key)
        self.assertEqual(TenantAccessKey.resolve(new_key).pk, self.key_obj.pk)
        self.assertIsNone(TenantAccessKey.resolve(self.raw_key))

    def test_rotate_updates_the_prefix_so_logs_track_the_new_key(self):
        new_key = self.key_obj.rotate()
        self.assertTrue(new_key.startswith(self.key_obj.key_prefix))

    def test_revoke_stops_the_key_and_records_when(self):
        self.key_obj.revoke()

        self.assertIsNone(TenantAccessKey.resolve(self.raw_key))
        reloaded = TenantAccessKey.objects.get(pk=self.key_obj.pk)
        self.assertFalse(reloaded.is_active)
        self.assertIsNotNone(reloaded.revoked_at)

    def test_a_revoked_row_left_active_still_does_not_authenticate(self):
        """resolve() filters on both flags, so neither alone leaves a key live."""
        TenantAccessKey.objects.filter(pk=self.key_obj.pk).update(revoked_at="2026-01-01T00:00:00Z")
        self.assertIsNone(TenantAccessKey.resolve(self.raw_key))

    def test_overlapping_handover_issues_a_second_key(self):
        """In-place rotation cuts the old key off immediately, so a handover
        with overlap issues a second key and revokes the first afterwards."""
        _, second_key = TenantAccessKey.issue(self.tenant)

        self.assertEqual(TenantAccessKey.resolve(self.raw_key).tenant, self.tenant)
        self.assertEqual(TenantAccessKey.resolve(second_key).tenant, self.tenant)

        self.key_obj.revoke()

        self.assertIsNone(TenantAccessKey.resolve(self.raw_key))
        self.assertEqual(TenantAccessKey.resolve(second_key).tenant, self.tenant)
