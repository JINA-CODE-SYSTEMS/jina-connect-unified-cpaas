# B4 (#184/#185 review): composite index supporting probe_config's
# GROUP BY event_type aggregate over a single provider config's events.
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("voice", "0005_voiceproviderconfig_default_flag_mutex"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="voicecallevent",
            index=models.Index(
                fields=["call", "event_type", "-occurred_at"],
                name="voice_event_call_type_t_idx",
            ),
        ),
    ]
