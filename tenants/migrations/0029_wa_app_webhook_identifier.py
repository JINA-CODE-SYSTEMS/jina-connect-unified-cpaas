"""Give every WhatsApp app its own opaque webhook callback identifier (#310).

Webhook authentication is single-valued for the whole deployment, and it cannot
stop being so while the only thing naming the sending app lives inside the body:
``entry[0].id`` and ``metadata.phone_number_id`` are untrustworthy until the
``X-Hub-Signature-256`` over that body has been verified, and verifying it needs
the app's secret — which needs to know the app. The way out is to put the
identity in the URL, so this column is what the URL carries::

    POST /wa/v2/webhooks/meta/<webhook_identifier>/

Three steps, because the constraint can only go on once every row has a value:
add the column nullable, fill it one random value per row, then make it unique
and NOT NULL. ``AddField`` with a callable default would not do — Django
evaluates a default *once* for the existing rows, so every app would be given
the same identifier and the unique index would refuse to build.

Nobody has to re-register anything. The unsuffixed ``/wa/v2/webhooks/meta/``
path keeps behaving exactly as it does today, verifying against the global
``META_APP_SECRET``; an app's existing callback URL is still its callback URL
after this migration. The per-app URL is an addition, and a client moves to it
when they are ready to. (The legacy path is single-app by construction and must
not be shared between clients: one deployment-wide secret cannot distinguish
them. That is what the rest of #305 is for.)

Reversing drops the column. No data is lost that matters — a reversed
deployment is back to the legacy path, which never consulted it — but any
per-app URL a client had already pasted into their dashboard stops resolving,
and re-applying generates *new* identifiers rather than the old ones. That is
the deliberate cost of an identifier nothing can recompute.
"""

from django.db import migrations, models

import tenants.models


def fill_webhook_identifiers(apps, schema_editor):
    """Give each existing app its own identifier, one random value per row.

    The generator comes from the live model rather than a copy, because the
    whole property being established here is that these values are generated
    the one way — a second implementation in a migration is a second place for
    the "not derived from anything" rule to quietly stop being true.
    """
    TenantWAApp = apps.get_model("tenants", "TenantWAApp")

    for app in TenantWAApp.objects.filter(webhook_identifier__isnull=True).iterator():
        app.webhook_identifier = tenants.models.generate_wa_webhook_identifier()
        app.save(update_fields=["webhook_identifier"])


class Migration(migrations.Migration):
    dependencies = [
        ("tenants", "0028_encrypt_wa_app_bsp_secrets"),
    ]

    operations = [
        # Nullable and non-unique first.
        migrations.AddField(
            model_name="tenantwaapp",
            name="webhook_identifier",
            field=models.CharField(
                max_length=64,
                null=True,
                help_text=(
                    "Opaque identifier carried in this app's own webhook callback URL. "
                    "Treat it as a secret: whoever holds it can address this app's receiver. "
                    "Generated once and never reused."
                ),
            ),
        ),
        migrations.RunPython(fill_webhook_identifiers, migrations.RunPython.noop),
        # Now that every row has one, say so in the schema: unique (which is
        # the index the receiver resolves through), NOT NULL, and not editable
        # — an identifier is issued, never typed in.
        migrations.AlterField(
            model_name="tenantwaapp",
            name="webhook_identifier",
            field=models.CharField(
                default=tenants.models.generate_wa_webhook_identifier,
                editable=False,
                max_length=64,
                unique=True,
                help_text=(
                    "Opaque identifier carried in this app's own webhook callback URL. "
                    "Treat it as a secret: whoever holds it can address this app's receiver. "
                    "Generated once and never reused."
                ),
            ),
        ),
    ]
