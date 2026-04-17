# Generated migration for #120 — Telegram scheduled message support

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("telegram", "0003_bot_token_encrypted_field"),
    ]

    operations = [
        migrations.AddField(
            model_name="telegramoutboundmessage",
            name="scheduled_time",
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text="If set, message will be sent at this time by the scheduler (#120)",
            ),
        ),
    ]
