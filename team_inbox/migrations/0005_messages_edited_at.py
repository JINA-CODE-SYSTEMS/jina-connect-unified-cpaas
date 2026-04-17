# Generated migration for #103 — Telegram edited_message support

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("team_inbox", "0004_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="messages",
            name="edited_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text="Timestamp when message was last edited (e.g. Telegram edited_message)",
            ),
        ),
    ]
