# Adds ``allowed_hours_local`` to ``Broadcast`` for voice time-of-day
# compliance (#171). Mirrored onto HistoricalBroadcast so the audit
# trail stays in sync.
#
# Hand-written for the same reason the other voice migrations are:
# ``manage.py makemigrations`` hangs on this dev box.

from django.db import migrations, models

HELP = (
    "Voice-only time-of-day window in the recipient's local TZ; "
    'e.g. ``{"start": "09:00", "end": "21:00"}``. Out-of-window '
    "dispatches are deferred to the next allowed time."
)


class Migration(migrations.Migration):
    dependencies = [
        ("broadcast", "0006_voice_platform_and_template"),
    ]

    operations = [
        migrations.AddField(
            model_name="broadcast",
            name="allowed_hours_local",
            field=models.JSONField(blank=True, help_text=HELP, null=True),
        ),
        migrations.AddField(
            model_name="historicalbroadcast",
            name="allowed_hours_local",
            field=models.JSONField(blank=True, help_text=HELP, null=True),
        ),
    ]
