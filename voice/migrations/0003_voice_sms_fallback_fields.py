# Adds SMS-fallback fields to ``VoiceProviderConfig`` (#172).
#
# Hand-written for the same reason ``voice/0001_initial.py`` is.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("voice", "0002_recordingconsent"),
        ("sms", "0004_alter_smsoutboundmessage_provider_used"),
    ]

    operations = [
        migrations.AddField(
            model_name="voiceproviderconfig",
            name="fallback_sms_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="voiceproviderconfig",
            name="fallback_sms_config",
            field=models.ForeignKey(
                blank=True,
                help_text="SMS app used to send the fallback message.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="voice_fallback_configs",
                to="sms.smsapp",
            ),
        ),
        migrations.AddField(
            model_name="voiceproviderconfig",
            name="fallback_sms_template",
            field=models.TextField(
                blank=True,
                help_text=('Jinja-style "{{var}}" placeholders allowed (first_name, from_number, to_number).'),
            ),
        ),
        migrations.AddField(
            model_name="voiceproviderconfig",
            name="fallback_on_causes",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text=(
                    "List of HangupCause values that trigger the SMS fallback. Empty list disables the cause filter."
                ),
            ),
        ),
    ]
