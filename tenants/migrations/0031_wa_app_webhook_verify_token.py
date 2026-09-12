"""Give every WhatsApp app its own webhook handshake token (#307).

A BSP will not deliver to a callback URL it has not verified, and it verifies
one by sending ``GET ?hub.mode=subscribe&hub.verify_token=…&hub.challenge=…``
and requiring the challenge back. Until now the receiver compared the presented
token to a single deployment-wide setting, which with #305's bring-your-own-app
handover means every client has to be handed the *same* value — a secret shared
across tenants, and enough for any one holder to complete the handshake for
another client's endpoint and point it wherever they like.

This column is that token, one per app. It is issued by
``tenants.models.generate_wa_webhook_verify_token`` (24 bytes from ``secrets``)
rather than chosen by a client, so it cannot be weak, reused, or shared.

The backfill reads every row and picks the blank ones out in Python rather than
filtering on ``webhook_verify_token=""``: an encrypted column holds the
*ciphertext* of the empty default, which no equality lookup on ``""`` matches, so
the filtered version would skip every row and leave the whole instance on the
shared token while reporting success.

Three steps, exactly as ``0029`` did for ``webhook_identifier`` and for the same
reason: ``AddField`` evaluates a callable default *once* for the rows that
already exist, so a one-step add would give every app on the instance the same
token — the defect this migration exists to remove, reintroduced by the
migration removing it. So the column arrives blank, each row is then filled with
its own value, and only then does the callable default go on for rows created
afterwards.

Nothing breaks where a value is still blank: the receiver falls back to the
deployment-wide ``META_WEBHOOK_VERIFY_TOKEN`` / ``GUPSHUP_WEBHOOK_VERIFY_TOKEN``
setting, which is the pre-#307 behaviour and what the legacy unsuffixed callback
path keeps doing permanently.

What a client has to do after this lands: a client who already pasted the
deployment-wide token beside their *per-app* URL re-copies the token from the
webhook-setup screen the next time their BSP re-verifies the URL. Deliveries in
flight are unaffected — the handshake happens at registration, not per delivery —
and the legacy path's token does not change at all.

The column is an ``EncryptedTextField``, following #289/#311: Fernet at rest, so
a dump or a read replica carries ciphertext rather than every tenant's handshake
token. Reading it needs ``FIELD_ENCRYPTION_KEY``, which is also why the backfill
below goes through ``save()`` rather than a bulk ``update`` per literal.

Reversing drops the column, and the handshake goes back to checking the one
global token for every app. No data is lost that cannot be reissued, but
re-applying generates *new* tokens: any client who had pasted theirs has to copy
the new one. That is the ordinary cost of dropping a credential column.
"""

import encrypted_model_fields.fields
from django.db import migrations

import tenants.models


def fill_webhook_verify_tokens(apps, schema_editor):
    """Give each existing app its own token, one fresh value per row.

    The generator comes from the live model rather than a copy of it: the
    property being established here is that these values are generated exactly
    one way, and a second implementation in a migration is a second place for
    "CSPRNG, never derived, never shared" to quietly stop being true.

    ``save()`` rather than ``queryset.update()`` because the column is
    encrypted — the field's ``get_db_prep_save`` is what turns the token into
    ciphertext — and because every row needs a *different* value anyway, so
    there is no bulk write to be had.

    Every row is read and the blank ones picked out in Python, rather than asked
    for with ``filter(webhook_verify_token="")``. An encrypted column cannot be
    filtered by value: the empty default this ``AddField`` writes is stored as
    the ciphertext *of* an empty string, which no equality lookup on ``""`` will
    find, so the filtered version of this loop would match nothing and the
    backfill would silently do nothing at all. A full scan of one app table is
    cheap; a migration that quietly skips every row is not.
    """
    TenantWAApp = apps.get_model("tenants", "TenantWAApp")

    for app in TenantWAApp.objects.iterator():
        if (app.webhook_verify_token or "").strip():
            continue
        app.webhook_verify_token = tenants.models.generate_wa_webhook_verify_token()
        app.save(update_fields=["webhook_verify_token"])


class Migration(migrations.Migration):
    dependencies = [
        ("tenants", "0030_wa_app_meta_app_secret"),
    ]

    operations = [
        # Blank first: a callable default here would hand every pre-existing row
        # the same token.
        migrations.AddField(
            model_name="tenantwaapp",
            name="webhook_verify_token",
            field=encrypted_model_fields.fields.EncryptedTextField(
                blank=True,
                default="",
                help_text=(
                    "The token this app's own webhook handshake checks hub.verify_token against. "
                    "Issued by this deployment, never chosen by the client, and encrypted at rest. "
                    "Handed over together with the callback URL by the webhook-setup endpoint."
                ),
            ),
        ),
        migrations.RunPython(fill_webhook_verify_tokens, migrations.RunPython.noop),
        # Now the default, for every row created from here on, and
        # ``editable=False``: a handshake token is issued, never typed in.
        migrations.AlterField(
            model_name="tenantwaapp",
            name="webhook_verify_token",
            field=encrypted_model_fields.fields.EncryptedTextField(
                blank=True,
                default=tenants.models.generate_wa_webhook_verify_token,
                editable=False,
                help_text=(
                    "The token this app's own webhook handshake checks hub.verify_token against. "
                    "Issued by this deployment, never chosen by the client, and encrypted at rest. "
                    "Handed over together with the callback URL by the webhook-setup endpoint."
                ),
            ),
        ),
    ]
