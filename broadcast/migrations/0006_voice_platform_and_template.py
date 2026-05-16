# Adds VOICE to BroadcastPlatformChoices and the ``voice_template`` FK
# on ``Broadcast`` so the dispatcher can route voice campaigns (#162).
#
# Hand-written for the same reason the other voice migrations are:
# ``manage.py makemigrations`` hangs on this dev box.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("broadcast", "0005_initial"),
        ("voice", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="broadcast",
            name="platform",
            field=models.CharField(
                choices=[
                    ("WHATSAPP", "WhatsApp"),
                    ("TELEGRAM", "Telegram"),
                    ("SMS", "SMS"),
                    ("RCS", "RCS"),
                    ("VOICE", "Voice"),
                ],
                default="WHATSAPP",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="historicalbroadcast",
            name="platform",
            field=models.CharField(
                choices=[
                    ("WHATSAPP", "WhatsApp"),
                    ("TELEGRAM", "Telegram"),
                    ("SMS", "SMS"),
                    ("RCS", "RCS"),
                    ("VOICE", "Voice"),
                ],
                default="WHATSAPP",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="broadcast",
            name="voice_template",
            field=models.ForeignKey(
                blank=True,
                help_text="Voice template for VOICE-platform broadcasts (TTS / pre-recorded audio).",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="broadcasts",
                to="voice.voicetemplate",
            ),
        ),
        # HistoricalBroadcast (django-simple-history) mirrors the FK as a
        # plain reference (db_constraint=False, related_name="+") so the
        # historical table doesn't share the parent's reverse manager.
        migrations.AddField(
            model_name="historicalbroadcast",
            name="voice_template",
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="+",
                to="voice.voicetemplate",
            ),
        ),
    ]
