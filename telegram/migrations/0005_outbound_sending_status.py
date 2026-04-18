from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("telegram", "0004_telegramoutboundmessage_scheduled_time"),
    ]

    operations = [
        migrations.AlterField(
            model_name="telegramoutboundmessage",
            name="status",
            field=models.CharField(
                choices=[
                    ("PENDING", "Pending"),
                    ("SENDING", "Sending"),
                    ("SENT", "Sent"),
                    ("FAILED", "Failed"),
                    ("BLOCKED", "Blocked"),
                ],
                default="PENDING",
                max_length=20,
            ),
        ),
    ]
