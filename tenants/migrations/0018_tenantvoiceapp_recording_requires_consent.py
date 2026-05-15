# Adds ``recording_requires_consent`` to ``TenantVoiceApp`` for the
# voice consent gate (#171). Hand-written for the same reason
# ``tenants/0017_tenantvoiceapp.py`` is.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tenants", "0017_tenantvoiceapp"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenantvoiceapp",
            name="recording_requires_consent",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "When set, adapters refuse to record a call unless a "
                    "``RecordingConsent`` row with ``consent_given=True`` "
                    "exists for the contact (#171)."
                ),
            ),
        ),
    ]
