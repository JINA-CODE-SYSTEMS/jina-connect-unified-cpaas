"""Give the META App ID its own column on TenantWAApp (#275).

``app_id`` is documented as the Gupshup app ID, but the META Resumable Upload
API needs the META App ID and ``MetaDirectAdapter.upload_media`` read it from
``app_id`` — one column holding two providers' identifiers, so a Meta app could
not carry both. Nullable, and the adapter still falls back to ``app_id``, so
apps configured before this migration keep uploading.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tenants", "0024_sent_partner_report"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenantwaapp",
            name="meta_app_id",
            field=models.CharField(
                blank=True,
                null=True,
                max_length=100,
                help_text="META App ID — used for the Resumable Upload API. Falls back to app_id when unset.",
            ),
        ),
    ]
