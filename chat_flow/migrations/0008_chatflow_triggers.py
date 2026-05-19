# #188: event-trigger declarative list on ChatFlow. Empty default keeps
# all existing flows on the legacy contact-assignment invocation path.
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("chat_flow", "0007_chatflow_platform"),
    ]

    operations = [
        migrations.AddField(
            model_name="chatflow",
            name="triggers",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text=(
                    "List of {type, config} entries that auto-spawn this flow on "
                    "matching inbound events. Empty = legacy assignment-only flow."
                ),
            ),
        ),
    ]
