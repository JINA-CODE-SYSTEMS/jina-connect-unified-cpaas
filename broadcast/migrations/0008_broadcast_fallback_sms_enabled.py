# Adds ``fallback_sms_enabled`` to ``Broadcast`` for per-campaign
# voice-fallback override (#172). Mirrored onto ``HistoricalBroadcast``.

from django.db import migrations, models

HELP = (
    "Override VoiceProviderConfig.fallback_sms_enabled for this "
    "broadcast. None = inherit; True/False forces the policy."
)


class Migration(migrations.Migration):
    dependencies = [
        ("broadcast", "0007_broadcast_allowed_hours_local"),
    ]

    operations = [
        migrations.AddField(
            model_name="broadcast",
            name="fallback_sms_enabled",
            field=models.BooleanField(blank=True, help_text=HELP, null=True),
        ),
        migrations.AddField(
            model_name="historicalbroadcast",
            name="fallback_sms_enabled",
            field=models.BooleanField(blank=True, help_text=HELP, null=True),
        ),
    ]
