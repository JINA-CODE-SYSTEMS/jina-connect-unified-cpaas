"""Encrypt the WhatsApp app's BSP secrets at rest (#289).

``TenantWAApp.bsp_credentials`` is a plain ``JSONField``, and what it holds is a
live Meta access token — the one credential that can send as the tenant, read
their message history and rewrite their templates. In plaintext it is readable
in the database, in every nightly backup, on every read replica and in any
support dump, for every organisation at once. Seven other models in this
project already keep provider credentials in ``EncryptedTextField``; WhatsApp,
the primary channel, was the only one that did not. ``meta/models.py`` stores
the *same* Meta token, for the same provider, encrypted.

So the secret keys move out of the JSON into two encrypted columns:
``bsp_credentials["access_token"]`` becomes ``bsp_access_token`` (META) and
``bsp_credentials["partner_app_token"]`` becomes ``bsp_partner_app_token``
(Gupshup). ``bsp_credentials`` itself stays, because it also carries non-secret
configuration — ``wa.services.template_sync`` falls back to ``waba_id`` in it —
and dropping the column would take that with it.

Nobody has to re-enter a token: the backfill below copies each value into its
encrypted column and removes it from the JSON, in the same pass. Nothing looks
this column up by value (checked: no ``bsp_credentials__…`` filter exists
anywhere), which matters because Fernet ciphertext is non-deterministic — two
encryptions of one token differ, so an equality lookup on these columns could
never work and none is introduced.

Reversing is lossless, unlike #301's one-way digest: the backwards pass
decrypts each value back into the JSON before the columns go, so a rollback
leaves every app sending exactly as it did.
"""

import encrypted_model_fields.fields
from django.db import migrations, models

# Legacy ``bsp_credentials`` key -> encrypted column that now owns it. Kept
# local to the migration: it describes the shape of the data on disk at this
# point in history, which must not drift when the model moves on.
SECRET_KEYS = {
    "access_token": "bsp_access_token",
    "partner_app_token": "bsp_partner_app_token",
}


def move_secrets_into_encrypted_columns(apps, schema_editor):
    """Carry every stored token into its encrypted column, JSON-side key gone."""
    TenantWAApp = apps.get_model("tenants", "TenantWAApp")

    for app in TenantWAApp.objects.exclude(bsp_credentials=None).iterator():
        creds = app.bsp_credentials
        if not isinstance(creds, dict) or not (set(creds) & set(SECRET_KEYS)):
            continue

        remaining = dict(creds)
        updated = ["bsp_credentials"]
        for key, field_name in SECRET_KEYS.items():
            value = remaining.pop(key, None)
            if value:
                setattr(app, field_name, value)
                updated.append(field_name)

        app.bsp_credentials = remaining
        app.save(update_fields=updated)


def move_secrets_back_into_json(apps, schema_editor):
    """Put the decrypted tokens back in ``bsp_credentials`` before the columns go.

    Without this a rollback would strand every tenant on no token at all, with
    sends falling back to the single global ``META_PERM_TOKEN`` or failing
    outright. The plaintext is recoverable here precisely because encryption is
    two-way, so the honest thing is to recover it.
    """
    TenantWAApp = apps.get_model("tenants", "TenantWAApp")

    for app in TenantWAApp.objects.iterator():
        restored = {}
        for key, field_name in SECRET_KEYS.items():
            value = getattr(app, field_name, "") or ""
            if value:
                restored[key] = value

        if not restored:
            continue

        creds = app.bsp_credentials if isinstance(app.bsp_credentials, dict) else {}
        app.bsp_credentials = {**creds, **restored}
        app.save(update_fields=["bsp_credentials"])


class Migration(migrations.Migration):
    dependencies = [
        ("tenants", "0027_tenant_access_key_hashed"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenantwaapp",
            name="bsp_access_token",
            field=encrypted_model_fields.fields.EncryptedTextField(
                blank=True,
                default="",
                help_text="META access token for this app. Encrypted at rest, and never returned by the API.",
            ),
        ),
        migrations.AddField(
            model_name="tenantwaapp",
            name="bsp_partner_app_token",
            field=encrypted_model_fields.fields.EncryptedTextField(
                blank=True,
                default="",
                help_text="Gupshup partner app token for this app. Encrypted at rest, and never returned by the API.",
            ),
        ),
        migrations.RunPython(move_secrets_into_encrypted_columns, move_secrets_back_into_json),
        # Says on the column what may now live in it. No schema change.
        migrations.AlterField(
            model_name="tenantwaapp",
            name="bsp_credentials",
            field=models.JSONField(
                blank=True,
                null=True,
                help_text=(
                    "Non-secret BSP configuration. Tokens and secrets are stored in the "
                    "encrypted bsp_access_token / bsp_partner_app_token columns instead."
                ),
            ),
        ),
    ]
