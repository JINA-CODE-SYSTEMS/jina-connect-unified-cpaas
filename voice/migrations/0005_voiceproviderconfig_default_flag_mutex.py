# B3 (#183): enforce mutex on is_default_outbound / is_default_inbound per tenant.
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("voice", "0004_rename_voice_recor_tenant__idx_voice_recor_tenant__a286dc_idx_and_more"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="voiceproviderconfig",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_default_outbound", True)),
                fields=("tenant",),
                name="voiceconfig_unique_default_outbound_per_tenant",
            ),
        ),
        migrations.AddConstraint(
            model_name="voiceproviderconfig",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_default_inbound", True)),
                fields=("tenant",),
                name="voiceconfig_unique_default_inbound_per_tenant",
            ),
        ),
    ]
