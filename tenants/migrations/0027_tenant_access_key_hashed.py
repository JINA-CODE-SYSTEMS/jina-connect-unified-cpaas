"""Store a digest of each tenant access key instead of the key itself (#301).

An access key is a bearer credential: presented in ``X-ACCESS-KEY`` it names
the tenant a JWT is scoped to, and presented to an MCP tool it *is* the whole
credential. Held in plaintext, any copy of the database — a nightly backup, a
read replica, a support dump — carried a working key for every organisation.
This is the same finding as #289, on the one secret that had escaped it.

So ``key`` becomes ``key_hash``: a keyed HMAC-SHA256 digest, unique and
indexed, which is what authentication now compares against. Existing keys keep
working, because the backfill below digests them in place before the plaintext
column goes — nobody has to reissue.

``key_prefix`` keeps the first few characters so a key can still be pointed at
in a log line or an admin list, and ``revoked_at`` records a revocation rather
than leaving ``is_active`` to imply one.

Reversing restores the column but not the plaintext: the digest is one-way and
nothing else recorded the keys. A rollback past this point means issuing new
keys, which is the honest cost of having stored them readably until now.
"""

import secrets

from django.db import migrations, models


def digest_existing_keys(apps, schema_editor):
    """Fill key_hash/key_prefix from the plaintext, before it is dropped."""
    # The digest has to match exactly what authentication will compute, so the
    # salt and algorithm come from the live model rather than a copy that could
    # drift away from it.
    from tenants.models import TenantAccessKey as LiveTenantAccessKey

    TenantAccessKey = apps.get_model("tenants", "TenantAccessKey")

    for row in TenantAccessKey.objects.all().iterator():
        if row.key:
            row.key_hash = LiveTenantAccessKey.hash_key(row.key)
            row.key_prefix = row.key[: LiveTenantAccessKey.PREFIX_LENGTH]
        else:
            # A blank key never authenticated anything — the lookup was by
            # value and no request arrives with an empty header. It still needs
            # a distinct digest to satisfy the unique constraint, and a random
            # one is right: it corresponds to no key anyone can present.
            row.key_hash = secrets.token_hex(32)
            row.key_prefix = ""
        row.save(update_fields=["key_hash", "key_prefix"])


class Migration(migrations.Migration):
    dependencies = [
        ("tenants", "0026_bsp_defaults_to_meta"),
    ]

    operations = [
        # Nullable and non-unique first: the constraint can only go on once
        # every existing row has been digested.
        migrations.AddField(
            model_name="tenantaccesskey",
            name="key_hash",
            field=models.CharField(
                max_length=64,
                null=True,
                help_text="HMAC-SHA256 digest of the access key. The key itself is never stored.",
            ),
        ),
        migrations.AddField(
            model_name="tenantaccesskey",
            name="key_prefix",
            field=models.CharField(
                blank=True,
                default="",
                max_length=12,
                help_text=(
                    "First characters of the key, so a key can be named in logs and admin without revealing it."
                ),
            ),
        ),
        migrations.AddField(
            model_name="tenantaccesskey",
            name="revoked_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text="When this key was revoked. Set together with is_active=False by revoke().",
            ),
        ),
        migrations.RunPython(digest_existing_keys, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="tenantaccesskey",
            name="key_hash",
            field=models.CharField(
                max_length=64,
                unique=True,
                help_text="HMAC-SHA256 digest of the access key. The key itself is never stored.",
            ),
        ),
        migrations.RemoveField(
            model_name="tenantaccesskey",
            name="key",
        ),
    ]
