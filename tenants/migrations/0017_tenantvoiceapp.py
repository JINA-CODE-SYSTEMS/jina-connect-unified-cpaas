# Adds TenantVoiceApp — the per-tenant feature flag + defaults for the
# voice channel. Hand-written for the same reason voice/0001_initial.py is:
# manage.py makemigrations hangs on this dev box.
#
# Depends on voice/0001_initial because TenantVoiceApp FKs
# voice.VoiceProviderConfig.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tenants", "0016_initial"),
        ("voice", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="TenantVoiceApp",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("description", models.TextField(blank=True, null=True)),
                ("name", models.CharField(max_length=100)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_active", models.BooleanField(default=True)),
                (
                    "is_enabled",
                    models.BooleanField(
                        default=False,
                        help_text="Master switch for the voice channel for this tenant.",
                    ),
                ),
                (
                    "recording_retention_days",
                    models.IntegerField(
                        default=90,
                        help_text="Days a recording is kept before retention sweep deletes it.",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_created_by",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_updated_by",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "tenant",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="voice_app",
                        to="tenants.tenant",
                    ),
                ),
                (
                    "default_outbound_config",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="tenants_as_default_outbound",
                        to="voice.voiceproviderconfig",
                    ),
                ),
                (
                    "default_inbound_config",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="tenants_as_default_inbound",
                        to="voice.voiceproviderconfig",
                    ),
                ),
            ],
            options={
                "verbose_name": "Tenant voice app",
                "verbose_name_plural": "Tenant voice apps",
            },
        ),
    ]
