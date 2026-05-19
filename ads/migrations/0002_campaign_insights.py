# #196: CampaignInsights, split out so it depends on ctwa.CtwaCampaign.
import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ads", "0001_initial"),
        ("ctwa", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="CampaignInsights",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=100)),
                (
                    "id",
                    models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False),
                ),
                ("day", models.DateField()),
                ("impressions", models.BigIntegerField(default=0)),
                ("clicks", models.BigIntegerField(default=0)),
                ("spend_minor", models.BigIntegerField(default=0)),
                ("cpc_minor", models.BigIntegerField(default=0)),
                ("ctr_bp", models.IntegerField(default=0)),
                ("polled_at", models.DateTimeField(auto_now=True)),
                (
                    "campaign",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="insights",
                        to="ctwa.ctwacampaign",
                    ),
                ),
            ],
            options={
                "verbose_name": "Campaign insights",
                "verbose_name_plural": "Campaign insights",
                "constraints": [
                    models.UniqueConstraint(
                        fields=["campaign", "day"],
                        name="ads_insights_unique_per_day",
                    )
                ],
                "indexes": [
                    models.Index(fields=["campaign", "-day"], name="ads_insights_campaign_day_idx"),
                ],
            },
        ),
    ]
