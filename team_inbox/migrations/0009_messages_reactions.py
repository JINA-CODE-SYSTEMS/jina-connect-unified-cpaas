from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("team_inbox", "0008_remove_conversation_model"),
    ]

    operations = [
        migrations.AddField(
            model_name="messages",
            name="reactions",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="List of reactions on this message: [{emoji, user_id, timestamp}]",
            ),
        ),
    ]
