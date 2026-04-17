# Generated migration for #119 — RCS template and template card models

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("rcs", "0002_rename_fallback_sms_id_rcsoutboundmessage_fallback_sms"),
        ("tenants", "__latest__"),
    ]

    operations = [
        migrations.CreateModel(
            name="RCSTemplate",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=255)),
                (
                    "message_type",
                    models.CharField(
                        choices=[
                            ("TEXT", "TEXT"),
                            ("RICH_CARD", "RICH_CARD"),
                            ("CAROUSEL", "CAROUSEL"),
                            ("MEDIA", "MEDIA"),
                            ("LOCATION", "LOCATION"),
                        ],
                        default="TEXT",
                        max_length=20,
                    ),
                ),
                ("body_text", models.TextField(blank=True, help_text="Template body text (supports {{placeholders}})")),
                ("suggestions", models.JSONField(blank=True, default=list, help_text="Default suggestion chips")),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="rcs_templates", to="tenants.tenant"
                    ),
                ),
                (
                    "rcs_app",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="templates", to="rcs.rcsapp"
                    ),
                ),
            ],
            options={
                "verbose_name": "RCS Template",
                "verbose_name_plural": "RCS Templates",
                "ordering": ["-created_at"],
                "unique_together": {("tenant", "name")},
            },
        ),
        migrations.CreateModel(
            name="RCSTemplateCard",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("order", models.PositiveSmallIntegerField(default=0)),
                ("title", models.CharField(blank=True, max_length=200)),
                ("description", models.TextField(blank=True, max_length=2000)),
                ("media_url", models.URLField(blank=True, max_length=512)),
                ("media_height", models.CharField(default="MEDIUM", max_length=10)),
                ("suggestions", models.JSONField(blank=True, default=list)),
                (
                    "template",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="cards", to="rcs.rcstemplate"
                    ),
                ),
            ],
            options={
                "verbose_name": "RCS Template Card",
                "ordering": ["order"],
            },
        ),
    ]
